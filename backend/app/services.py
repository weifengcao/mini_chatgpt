from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.gateways.model_gateway import ModelGateway, ModelMessage
from app.gateways.tool_gateway import ToolError, ToolGateway
from app.models import (
    Agent,
    AgentAccessAssignment,
    AgentRun,
    AgentRunEvidence,
    AgentStepRun,
    ChatSession,
    ChatSessionParticipant,
    Message,
    ModelRequest,
    ToolRun,
    User,
    utcnow,
)


class AgentLoopLimitError(Exception):
    code = "AGENT_LOOP_LIMIT_EXCEEDED"


class AgentRunTimeoutError(Exception):
    code = "AGENT_RUN_TIMED_OUT"


def request_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def user_can_access_agent(db: Session, user: User, agent_id: str) -> bool:
    if user.role == "SUPER_ADMIN":
        return True
    assignment = db.scalar(
        select(AgentAccessAssignment).where(
            AgentAccessAssignment.company_id == user.company_id,
            AgentAccessAssignment.agent_id == agent_id,
            AgentAccessAssignment.user_id == user.id,
        )
    )
    return assignment is not None


def require_agent_access(db: Session, user: User, agent_id: str) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.company_id != user.company_id or agent.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not user_can_access_agent(db, user, agent_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent access required")
    return agent


def require_session_access(db: Session, user: User, session_id: str) -> tuple[ChatSession, Agent]:
    session = db.get(ChatSession, session_id)
    if session is None or session.company_id != user.company_id or session.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    agent = require_agent_access(db, user, session.agent_id)
    return session, agent


def touch_participant(db: Session, settings: Settings, user: User, session: ChatSession) -> None:
    participant_count = db.scalar(
        select(func.count(ChatSessionParticipant.id)).where(
            ChatSessionParticipant.company_id == user.company_id,
            ChatSessionParticipant.session_id == session.id,
            ChatSessionParticipant.status == "active",
        )
    )
    existing = db.scalar(
        select(ChatSessionParticipant).where(
            ChatSessionParticipant.company_id == user.company_id,
            ChatSessionParticipant.session_id == session.id,
            ChatSessionParticipant.user_id == user.id,
        )
    )
    if existing is None and participant_count >= settings.session_participant_cap:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SESSION_FULL")
    if existing is None:
        db.add(ChatSessionParticipant(company_id=user.company_id, session_id=session.id, user_id=user.id))
    else:
        existing.last_seen_at = utcnow()
        existing.status = "active"
    db.commit()


def next_sequence(session: ChatSession) -> int:
    session.last_sequence_number += 1
    return session.last_sequence_number


def create_step(
    db: Session,
    run: AgentRun,
    step_type: str,
    input_summary: str = "",
    output_summary: str = "",
    input_json: dict[str, Any] | None = None,
    output_json: dict[str, Any] | None = None,
    status_value: str = "complete",
    error_code: str | None = None,
) -> AgentStepRun:
    current_max = db.scalar(
        select(func.coalesce(func.max(AgentStepRun.step_index), 0)).where(AgentStepRun.agent_run_id == run.id)
    )
    max_steps = int((run.loop_limits_json or {}).get("max_agent_steps", 0))
    if max_steps and int(current_max) >= max_steps:
        raise AgentLoopLimitError("Agent step limit exceeded")
    step = AgentStepRun(
        company_id=run.company_id,
        agent_run_id=run.id,
        step_index=int(current_max) + 1,
        loop_iteration=int(current_max) + 1,
        step_type=step_type,
        input_summary=input_summary,
        output_summary=output_summary,
        input_json=input_json or {},
        output_json=output_json or {},
        status=status_value,
        error_code=error_code,
    )
    db.add(step)
    db.flush()
    return step


def create_evidence(
    db: Session,
    run: AgentRun,
    evidence_type: str,
    source_type: str,
    source_id: str,
    content_summary: str,
    step: AgentStepRun | None = None,
) -> AgentRunEvidence:
    evidence = AgentRunEvidence(
        company_id=run.company_id,
        agent_run_id=run.id,
        step_run_id=step.id if step else None,
        evidence_type=evidence_type,
        source_type=source_type,
        source_id=source_id,
        content_summary=content_summary[:2000],
        content_hash=content_hash(content_summary),
    )
    db.add(evidence)
    db.flush()
    if step:
        step.evidence_ids_json = [*step.evidence_ids_json, evidence.id]
    return evidence


def loop_limits(settings: Settings, agent: Agent) -> dict[str, int]:
    limits = {
        "max_agent_steps": settings.agent_max_steps,
        "max_model_calls_per_run": settings.agent_max_model_calls,
        "max_tool_calls_per_run": settings.agent_max_tool_calls,
        "max_same_tool_calls_per_run": settings.agent_max_same_tool_calls,
        "max_run_duration_seconds": settings.agent_max_run_duration_seconds,
    }
    for key, value in (agent.loop_config_json or {}).items():
        if key in limits and isinstance(value, int) and value > 0:
            limits[key] = value
    return limits


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_timeout_seconds(run: AgentRun, fallback: int) -> int:
    return int((run.loop_limits_json or {}).get("max_run_duration_seconds") or fallback)


def _run_is_expired(run: AgentRun, fallback_timeout_seconds: int) -> bool:
    timeout_seconds = _run_timeout_seconds(run, fallback_timeout_seconds)
    if timeout_seconds <= 0:
        return False
    started_at = _as_utc(run.started_at or run.created_at)
    return utcnow() >= started_at + timedelta(seconds=timeout_seconds)


def _raise_if_run_expired(run: AgentRun) -> None:
    if _run_is_expired(run, 0):
        raise AgentRunTimeoutError("Agent run exceeded max duration")


def _mark_run_interrupted(
    db: Session,
    run: AgentRun,
    assistant_message: Message | None,
    code: str,
    output_summary: str,
) -> None:
    has_content = bool(assistant_message and assistant_message.content)
    if assistant_message is not None:
        assistant_message.status = "partial" if has_content else "failed"
        assistant_message.error_code = code
    run.status = "partial" if has_content else "failed"
    run.error_code = code
    run.completed_at = utcnow()
    run.metrics_json = {**(run.metrics_json or {}), "completed": False}
    try:
        create_step(db, run, "error", status_value="failed", output_summary=output_summary, error_code=code)
    except AgentLoopLimitError:
        pass


def expire_stale_running_runs(db: Session, settings: Settings, company_id: str, session_id: str) -> None:
    running_runs = list(
        db.scalars(
            select(AgentRun).where(
                AgentRun.company_id == company_id,
                AgentRun.session_id == session_id,
                AgentRun.status == "running",
            )
        )
    )
    expired = False
    for run in running_runs:
        if not _run_is_expired(run, settings.agent_max_run_duration_seconds):
            continue
        assistant_message = db.get(Message, run.assistant_message_id) if run.assistant_message_id else None
        _mark_run_interrupted(
            db,
            run,
            assistant_message,
            "AGENT_RUN_TIMED_OUT",
            "Agent run exceeded max duration and was recovered before a new send.",
        )
        expired = True
    if expired:
        db.commit()


def prepare_chat_run(
    db: Session,
    settings: Settings,
    user: User,
    session_id: str,
    content: str,
    idempotency_key: str,
) -> tuple[ChatSession, Agent, Message, Message, AgentRun, bool]:
    session, agent = require_session_access(db, user, session_id)
    touch_participant(db, settings, user, session)
    expire_stale_running_runs(db, settings, user.company_id, session_id)

    hashed = request_hash(content)
    existing_message = db.scalar(
        select(Message).where(
            Message.company_id == user.company_id,
            Message.session_id == session_id,
            Message.user_id == user.id,
            Message.idempotency_key == idempotency_key,
        )
    )
    if existing_message:
        if existing_message.request_hash != hashed:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="IDEMPOTENCY_KEY_CONFLICT")
        existing_run = db.scalar(select(AgentRun).where(AgentRun.triggering_message_id == existing_message.id))
        if existing_run and existing_run.assistant_message_id:
            assistant = db.get(Message, existing_run.assistant_message_id)
            if assistant and existing_run.status in {"complete", "failed", "partial"}:
                return session, agent, existing_message, assistant, existing_run, True
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SESSION_BUSY")

    running = db.scalar(
        select(AgentRun).where(
            AgentRun.company_id == user.company_id,
            AgentRun.session_id == session_id,
            AgentRun.status == "running",
        )
    )
    if running is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SESSION_BUSY")

    user_message = Message(
        company_id=user.company_id,
        session_id=session_id,
        user_id=user.id,
        sequence_number=next_sequence(session),
        idempotency_key=idempotency_key,
        request_hash=hashed,
        role="user",
        content=content,
        status="complete",
    )
    db.add(user_message)
    db.flush()

    assistant_message = Message(
        company_id=user.company_id,
        session_id=session_id,
        user_id=None,
        sequence_number=next_sequence(session),
        role="assistant",
        content="",
        status="streaming",
    )
    db.add(assistant_message)
    db.flush()

    run = AgentRun(
        company_id=user.company_id,
        session_id=session_id,
        agent_id=agent.id,
        triggering_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        idempotency_key=idempotency_key,
        status="running",
        loop_limits_json=loop_limits(settings, agent),
        loop_counters_json={"model_calls": 0, "tool_calls": 0, "same_tool_calls": {}},
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SESSION_BUSY") from exc
    db.refresh(run)
    db.refresh(user_message)
    db.refresh(assistant_message)
    db.refresh(session)
    return session, agent, user_message, assistant_message, run, False


def find_calculator_expression(text: str) -> str | None:
    cleaned = text.strip()
    lowered = cleaned.lower()
    if lowered.startswith("/calc"):
        return cleaned[5:].strip()
    if lowered.startswith("calculate"):
        return cleaned[9:].strip().strip(":")
    if re.fullmatch(r"[0-9\s+\-*/().%^]+", cleaned):
        return cleaned.replace("^", "**")
    return None


async def stream_existing(assistant: Message, run: AgentRun) -> AsyncIterator[str]:
    yield sse_event("message_started", {"message_id": assistant.id, "agent_run_id": run.id, "replayed": True})
    if assistant.content:
        yield sse_event("token", {"message_id": assistant.id, "text": assistant.content})
    if run.status == "complete":
        yield sse_event("message_completed", {"message_id": assistant.id, "agent_run_id": run.id})
    else:
        yield sse_event(
            "error",
            {
                "message_id": assistant.id,
                "agent_run_id": run.id,
                "code": run.error_code or assistant.error_code or "RUN_NOT_COMPLETE",
                "message": "The previous run did not complete successfully.",
            },
        )


async def stream_agent_run(
    db: Session,
    settings: Settings,
    user: User,
    session: ChatSession,
    agent: Agent,
    user_message: Message,
    assistant_message: Message,
    run: AgentRun,
) -> AsyncIterator[str]:
    model_gateway = ModelGateway(settings)
    tool_gateway = ToolGateway()

    try:
        yield sse_event("message_started", {"message_id": assistant_message.id, "agent_run_id": run.id})
        yield sse_event("agent_run_started", {"agent_run_id": run.id, "message_id": assistant_message.id})

        context_step = create_step(
            db,
            run,
            "context_build",
            input_summary="Build model context",
            output_summary="Context built from agent prompt and recent messages",
        )
        create_evidence(db, run, "user_message", "message", user_message.id, user_message.content, context_step)
        db.commit()
        _raise_if_run_expired(run)

        expression = find_calculator_expression(user_message.content)
        if expression and "calculator" in (agent.tool_allowlist_json or []):
            assistant_text = await _run_calculator_path(
                db, tool_gateway, user, session, agent, user_message, assistant_message, run, expression
            )
            yield sse_event("tool_completed", {"agent_run_id": run.id, "tool_name": "calculator"})
            for token in assistant_text.split(" "):
                yield sse_event("token", {"message_id": assistant_message.id, "text": token + " "})
        else:
            assistant_text = await _run_model_path(db, settings, model_gateway, agent, user_message, assistant_message, run)
            for token in assistant_text:
                yield sse_event("token", {"message_id": assistant_message.id, "text": token})

        assistant_message.status = "complete"
        run.status = "complete"
        run.completed_at = utcnow()
        run.metrics_json = {**run.metrics_json, "completed": True}
        final_step = create_step(db, run, "final_response", output_summary=assistant_message.content[:500])
        create_evidence(db, run, "final_response", "message", assistant_message.id, assistant_message.content, final_step)
        db.commit()
        yield sse_event("message_completed", {"message_id": assistant_message.id, "agent_run_id": run.id})
    except asyncio.CancelledError:
        _mark_run_interrupted(
            db,
            run,
            assistant_message,
            "CLIENT_DISCONNECTED",
            "Client disconnected before the stream completed.",
        )
        db.commit()
        raise
    except Exception as exc:
        code = getattr(exc, "code", "AGENT_RUN_FAILED")
        _mark_run_interrupted(db, run, assistant_message, code, str(exc))
        db.commit()
        yield sse_event(
            "error",
            {
                "message_id": assistant_message.id,
                "agent_run_id": run.id,
                "code": code,
                "message": "The assistant could not complete this response.",
            },
        )


async def _run_calculator_path(
    db: Session,
    tool_gateway: ToolGateway,
    user: User,
    session: ChatSession,
    agent: Agent,
    user_message: Message,
    assistant_message: Message,
    run: AgentRun,
    expression: str,
) -> str:
    _raise_if_run_expired(run)
    max_tool_calls = int((run.loop_limits_json or {}).get("max_tool_calls_per_run", 0))
    current_tool_calls = int(run.loop_counters_json.get("tool_calls", 0))
    if max_tool_calls and current_tool_calls >= max_tool_calls:
        raise ToolError("AGENT_LOOP_LIMIT_EXCEEDED", "Tool call limit exceeded")
    tool_step = create_step(
        db,
        run,
        "tool_call",
        input_summary=f"calculator({expression})",
        input_json={"expression": expression},
    )
    tool_run = ToolRun(
        company_id=user.company_id,
        session_id=session.id,
        agent_id=agent.id,
        agent_run_id=run.id,
        step_run_id=tool_step.id,
        message_id=user_message.id,
        user_id=user.id,
        tool_name="calculator",
        arguments_json={"expression": expression},
    )
    db.add(tool_run)
    db.flush()
    run.loop_counters_json = {
        **run.loop_counters_json,
        "tool_calls": int(run.loop_counters_json.get("tool_calls", 0)) + 1,
    }
    try:
        result = tool_gateway.execute("calculator", {"expression": expression}, agent.tool_allowlist_json or [])
        tool_run.status = "complete"
        tool_run.result_json = result.result
        tool_run.completed_at = utcnow()
        tool_step.status = "complete"
        tool_step.output_summary = result.result["text"]
        tool_step.output_json = result.result
        create_evidence(db, run, "tool_result", "tool_run", tool_run.id, result.result["text"], tool_step)
    except ToolError as exc:
        tool_run.status = "failed"
        tool_run.error_code = exc.code
        tool_run.completed_at = utcnow()
        raise
    assistant_text = f"The calculator result is {tool_run.result_json['text']}."
    assistant_message.content = assistant_text
    db.commit()
    return assistant_text


async def _run_model_path(
    db: Session,
    settings: Settings,
    model_gateway: ModelGateway,
    agent: Agent,
    user_message: Message,
    assistant_message: Message,
    run: AgentRun,
) -> list[str]:
    _raise_if_run_expired(run)
    model_step = create_step(db, run, "model_call", input_summary=user_message.content[:500])
    max_model_calls = int((run.loop_limits_json or {}).get("max_model_calls_per_run", 0))
    current_model_calls = int(run.loop_counters_json.get("model_calls", 0))
    if max_model_calls and current_model_calls >= max_model_calls:
        raise AgentLoopLimitError("Model call limit exceeded")
    model_request = ModelRequest(
        company_id=run.company_id,
        agent_run_id=run.id,
        step_run_id=model_step.id,
        provider=agent.model_provider or settings.ai_provider,
        model_name=agent.model_name or settings.ai_model,
        request_summary=user_message.content[:1000],
    )
    db.add(model_request)
    db.flush()
    model_step.model_request_id = model_request.id
    run.loop_counters_json = {
        **run.loop_counters_json,
        "model_calls": int(run.loop_counters_json.get("model_calls", 0)) + 1,
    }
    db.commit()

    tokens: list[str] = []
    messages = [
        ModelMessage(role="system", content=agent.system_prompt),
        ModelMessage(role="user", content=user_message.content),
    ]
    async for token in model_gateway.stream_chat(messages, model_name=agent.model_name, provider=agent.model_provider):
        _raise_if_run_expired(run)
        if run.first_token_at is None:
            run.first_token_at = utcnow()
            model_request.first_token_at = run.first_token_at
        tokens.append(token)
        assistant_message.content += token

    response = "".join(tokens)
    model_request.status = "complete"
    model_request.response_summary = response[:1000]
    model_request.completed_at = utcnow()
    model_step.status = "complete"
    model_step.output_summary = response[:500]
    create_evidence(db, run, "model_output", "model_request", model_request.id, response, model_step)
    db.commit()
    return tokens
