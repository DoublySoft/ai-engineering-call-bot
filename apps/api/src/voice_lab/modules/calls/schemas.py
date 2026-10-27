import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from voice_lab.core.security import normalize_e164

DEFAULT_SYSTEM_PROMPT = (
    "Eres un asistente de voz de un laboratorio de pruebas. Responde en el idioma del usuario, "
    "con frases cortas y naturales (máximo dos frases), sin listas ni formato markdown, porque tu "
    "respuesta se convertirá en voz en una llamada telefónica."
)


class SessionConfig(BaseModel):
    voice_provider: Literal["custom", "retell"] = "custom"
    engine: Literal["pipeline", "realtime"] = "pipeline"
    stt_provider: str | None = None
    stt_model: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    language: str | None = None
    system_prompt: str = Field(DEFAULT_SYSTEM_PROMPT, max_length=4000)
    greeting: str | None = Field("Hola, soy el asistente del laboratorio de voz. ¿En qué puedo ayudarte?", max_length=500)
    max_output_tokens: int = Field(300, ge=16, le=2000)


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
