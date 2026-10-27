from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The repo-root .env is shared by docker compose, the API and the web app.
_parents = Path(__file__).resolve().parents
ROOT_ENV = _parents[5] / ".env" if len(_parents) > 5 else Path(".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT_ENV, ".env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://voice_ai:change-me@127.0.0.1:55432/voice_ai_lab"
    admin_api_token: str = ""
    public_base_url: str = ""
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])

    outbound_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_call_duration_seconds: int = 300
    max_call_cost_usd: Decimal = Decimal("0.50")
    record_audio: bool = False

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    retell_api_key: str = ""

    stt_provider: str = "openai"
    stt_model: str = "gpt-transcribe"
    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    tts_provider: str = "openai"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    agent_language: str = "es"

    @field_validator("outbound_allowlist", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_phone_number)

    @property
    def public_ws_base(self) -> str:
        return self.public_base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
