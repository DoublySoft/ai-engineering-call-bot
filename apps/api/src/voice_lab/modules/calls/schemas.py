import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from voice_lab.core.security import normalize_e164


class SessionConfig(BaseModel):
    """Which agent to run, plus optional per-call overrides of its defaults.
    Only fields that are explicitly sent override the agent (see resolve_config)."""

    agent_id: uuid.UUID | None = None  # None -> the default agent
    voice_provider: Literal["custom", "retell"] | None = None
    engine: Literal["pipeline", "realtime"] | None = None
    stt_provider: str | None = None
    stt_model: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    s2s_model: str | None = None
    retell_voice_id: str | None = None
    language: str | None = None
    greeting: str | None = Field(None, max_length=500)
    max_output_tokens: int | None = Field(None, ge=16, le=2000)


class OutboundCallRequest(BaseModel):
    to: str
    config: SessionConfig = SessionConfig()

    @field_validator("to")
    @classmethod
    def _e164(cls, v: str) -> str:
        return normalize_e164(v)


class CallSummary(BaseModel):
    id: uuid.UUID
    direction: str
    status: str
    to_number: str | None
    from_number: str | None
    engine: str | None
    llm_model: str | None
    created_at: datetime
    duration_seconds: int | None
    total_cost_usd: Decimal | None
    error: str | None


class CallDetail(BaseModel):
    call: dict[str, Any]
    sessions: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    events: list[dict[str, Any]]
    usage: list[dict[str, Any]]
    cost_by_operation: dict[str, dict[str, Any]]
    events_token: str
