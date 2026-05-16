from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models import Agent, AgentAccessAssignment, AgentRun, AgentRunEvidence, AgentStepRun, ChatSession, Message, User
from app.schemas import (
    AgentRunOut,
    AgentStepOut,
    ChatSessionCreate,
    ChatSessionOut,
    EvidenceOut,
    MessageCreate,
    MessageOut,
    MetricsOut,
)
from app.services import (
    prepare_chat_run,
    require_agent_access,
    require_session_access,
    sse_event,
    stream_agent_run,
    stream_existing,
    touch_participant,
    user_can_access_agent,
)

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/chat-sessions", response_model=list[ChatSessionOut])
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ChatSession]:
    return list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.company_id == user.company_id, ChatSession.status != "deleted")
            .order_by(ChatSession.updated_at.desc())
        )
    )


@router.post("/chat-sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> ChatSession:
    agent = require_agent_access(db, user, payload.agent_id)
    title = payload.title or f"Chat with {agent.name}"
    session = ChatSession(company_id=user.company_id, agent_id=agent.id, title=title, created_by=user.id)
    db.add(session)
    db.flush()
    touch_participant(db, settings, user, session)
    db.refresh(session)
    return session


@router.get("/chat-sessions/{session_id}", response_model=ChatSessionOut)
def get_session(session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ChatSession:
    session, _ = require_session_access(db, user, session_id)
    return session


@router.post("/chat-sessions/{session_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(
    session_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> None:
    session, _ = require_session_access(db, user, session_id)
    touch_participant(db, settings, user, session)


@router.get("/chat-sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Message]:
    require_session_access(db, user, session_id)
    return list(
        db.scalars(
            select(Message)
            .where(Message.company_id == user.company_id, Message.session_id == session_id)
            .order_by(Message.sequence_number.asc(), Message.created_at.asc())
        )
    )


@router.post("/chat-sessions/{session_id}/messages/stream")
def stream_message(
    session_id: str,
    payload: MessageCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    if not idempotency_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key header is required")
    try:
        session, agent, user_message, assistant_message, run, replay = prepare_chat_run(
            db, settings, user, session_id, payload.content, idempotency_key
        )
    except HTTPException as exc:
        if exc.detail == "SESSION_BUSY":
            async def busy_stream():
                yield sse_event("error", {"code": "SESSION_BUSY", "message": "The session agent is still responding."})

            return StreamingResponse(busy_stream(), media_type="text/event-stream")
        raise

    generator = (
        stream_existing(assistant_message, run)
        if replay
        else stream_agent_run(db, settings, user, session, agent, user_message, assistant_message, run)
    )
    return StreamingResponse(generator, media_type="text/event-stream")


@router.post("/chat-sessions/{session_id}/messages", response_model=MessageOut)
async def create_message_non_streaming(
    session_id: str,
    payload: MessageCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> Message:
    if not idempotency_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key header is required")
    session, agent, user_message, assistant_message, run, replay = prepare_chat_run(
        db, settings, user, session_id, payload.content, idempotency_key
    )
    if replay:
        return assistant_message
    async for _ in stream_agent_run(db, settings, user, session, agent, user_message, assistant_message, run):
        pass
    db.refresh(assistant_message)
    return assistant_message


@router.get("/agent-runs", response_model=list[AgentRunOut])
def list_agent_runs(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[AgentRun]:
    query = select(AgentRun).where(AgentRun.company_id == user.company_id).order_by(AgentRun.created_at.desc())
    if user.role != "SUPER_ADMIN":
        allowed_agent_ids = select(AgentAccessAssignment.agent_id).where(
            AgentAccessAssignment.company_id == user.company_id,
            AgentAccessAssignment.user_id == user.id,
        )
        query = query.where(AgentRun.agent_id.in_(allowed_agent_ids))
    return list(db.scalars(query.limit(100)))


@router.get("/agent-runs/{run_id}", response_model=AgentRunOut)
def get_agent_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None or run.company_id != user.company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    if not user_can_access_agent(db, user, run.agent_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent access required")
    return run


@router.get("/agent-runs/{run_id}/steps", response_model=list[AgentStepOut])
def get_agent_run_steps(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[AgentStepRun]:
    run = get_agent_run(run_id, db, user)
    return list(
        db.scalars(
            select(AgentStepRun)
            .where(AgentStepRun.company_id == user.company_id, AgentStepRun.agent_run_id == run.id)
            .order_by(AgentStepRun.step_index.asc())
        )
    )


@router.get("/agent-runs/{run_id}/evidence", response_model=list[EvidenceOut])
def get_agent_run_evidence(
    run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[AgentRunEvidence]:
    run = get_agent_run(run_id, db, user)
    return list(
        db.scalars(
            select(AgentRunEvidence)
            .where(AgentRunEvidence.company_id == user.company_id, AgentRunEvidence.agent_run_id == run.id)
            .order_by(AgentRunEvidence.created_at.asc())
        )
    )


@router.get("/agent-runs/{run_id}/diagnostics")
def get_agent_run_diagnostics(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    run = get_agent_run(run_id, db, user)
    return {
        "run": AgentRunOut.model_validate(run).model_dump(mode="json"),
        "steps": [AgentStepOut.model_validate(step).model_dump(mode="json") for step in get_agent_run_steps(run_id, db, user)],
        "evidence": [
            EvidenceOut.model_validate(item).model_dump(mode="json") for item in get_agent_run_evidence(run_id, db, user)
        ],
    }


@router.get("/metrics/company", response_model=MetricsOut)
def company_metrics(db: Session = Depends(get_db), user: User = Depends(require_admin)) -> MetricsOut:
    data = {
        "agents": db.scalar(select(func.count(Agent.id)).where(Agent.company_id == user.company_id)),
        "chat_sessions": db.scalar(select(func.count(ChatSession.id)).where(ChatSession.company_id == user.company_id)),
        "messages": db.scalar(select(func.count(Message.id)).where(Message.company_id == user.company_id)),
        "agent_runs": db.scalar(select(func.count(AgentRun.id)).where(AgentRun.company_id == user.company_id)),
        "failed_agent_runs": db.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.company_id == user.company_id, AgentRun.status == "failed")
        ),
    }
    return MetricsOut(data=data)
