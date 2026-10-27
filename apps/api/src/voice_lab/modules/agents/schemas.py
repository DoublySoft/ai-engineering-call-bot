import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_INSTRUCTIONS = (
    "Eres el asistente de un laboratorio de pruebas de agentes de voz con IA. Ayuda a la persona con lo que "
    "necesite. Si te pregunta qué eres, explica que eres un agente de voz experimental."
)
DEFAULT_GREETING = "Hola, soy el asistente del laboratorio de voz. ¿En qué puedo ayudarte?"
LANGUAGES = {"es": "español", "en": "inglés", "ca": "catalán", "fr": "francés", "pt": "portugués", "it": "italiano", "de": "alemán"}


class AgentSettings(BaseModel):
    """What an agent version says and which models it uses by default.
    Model fields left empty fall back to the server defaults in .env."""

    model_config = ConfigDict(extra="ignore")  # older versions stored extra keys

    instructions: str = Field(DEFAULT_INSTRUCTIONS, min_length=1, max_length=8000)
    knowledge: str = Field("", max_length=30000, description="Reference text appended to the prompt")
    greeting: str | None = Field(DEFAULT_GREETING, max_length=500)
    language: str = "es"
    stt_provider: str | None = None
    stt_model: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    s2s_model: str | None = None  # engine "realtime"
    retell_voice_id: str | None = None  # voice provider "retell"
    max_output_tokens: int = Field(300, ge=16, le=2000)

    @field_validator("language", mode="before")
    @classmethod
    def _blank_language_is_default(cls, v: object) -> object:
        # Lenient on purpose: stored versions may hold any language value accepted before
        # agents existed (e.g. "ja" from AGENT_LANGUAGE). Writes are checked in AgentWrite.
        return v if isinstance(v, str) and v.strip() else "es"

    @field_validator("greeting")
    @classmethod
    def _blank_greeting_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class AgentWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    engine: Literal["pipeline", "realtime"] = "pipeline"
    voice_provider: Literal["custom", "retell"] = "custom"
    settings: AgentSettings = AgentSettings()

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name is required")
        return v.strip()

    @field_validator("settings")
    @classmethod
    def _language_code(cls, v: AgentSettings) -> AgentSettings:
        # Any ISO 639 code is fine (the editor lists common ones); free text is not.
        if not LANGUAGE_CODE.match(v.language):
            raise ValueError("language must be an ISO 639 code such as 'es', 'en' or 'pt-BR'")
        return v


class AgentVersion(BaseModel):
    configuration_id: uuid.UUID
    version: int
    created_at: datetime


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_default: bool
    archived: bool
    created_at: datetime
    updated_at: datetime
    version: int
    configuration_id: uuid.UUID
    engine: str
    voice_provider: str
    settings: AgentSettings
    calls: int = 0


class AgentDetail(AgentOut):
    versions: list[AgentVersion]
    effective: dict[str, Any] = Field(description="Settings after server defaults are applied, incl. the final prompt")
