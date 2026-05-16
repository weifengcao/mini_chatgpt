from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    email: str
    role: str
    status: str


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    system_prompt: str = Field(default="You are a helpful assistant inside mini_chatgpt.")
    model_provider: str = "fake"
    model_name: str = "fake-mini"
    tool_allowlist_json: list[str] = Field(default_factory=lambda: ["calculator"])


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    tool_allowlist_json: list[str] | None = None
    status: str | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    name: str
    description: str
    system_prompt: str
    model_provider: str
    model_name: str
    tool_allowlist_json: list[str]
    status: str
    created_at: datetime


class AgentAccessUpdate(BaseModel):
    user_ids: list[str]


class ChatSessionCreate(BaseModel):
    agent_id: str
    title: str | None = None


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    agent_id: str
    title: str
    status: str
    last_sequence_number: int
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    user_id: str | None
    sequence_number: int
    role: str
    content: str
    status: str
    error_code: str | None
    created_at: datetime


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    agent_id: str
    triggering_message_id: str
    assistant_message_id: str | None
    status: str
    error_code: str | None
    metrics_json: dict[str, Any]
    created_at: datetime


class AgentStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_run_id: str
    step_index: int
    loop_iteration: int
    step_type: str
    status: str
    input_summary: str
    output_summary: str
    error_code: str | None


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_run_id: str
    step_run_id: str | None
    evidence_type: str
    source_type: str
    source_id: str
    content_summary: str
    content_hash: str


class MetricsOut(BaseModel):
    data: dict[str, Any]
