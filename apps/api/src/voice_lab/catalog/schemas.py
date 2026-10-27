"""Typed model catalog.

Kept deliberately separate:
  * ModelDefinition  -> what a model is and what it can do (capabilities)
  * PriceComponent    -> one billable dimension with an explicit unit
  * PricingVersion    -> a dated, sourced set of components (prices change)
Real consumption lives in UsageEvent rows, never here.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class ModelProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    TWILIO = "twilio"
    RETELL = "retell"


class ModelKind(StrEnum):
    LLM = "llm"
    STT = "stt"
    TTS = "tts"
    SPEECH_TO_SPEECH = "speech_to_speech"
    TELEPHONY = "telephony"
    MANAGED = "managed"


class BillingDimension(StrEnum):
    INPUT_TEXT_TOKENS = "input_text_tokens"
    CACHED_INPUT_TEXT_TOKENS = "cached_input_text_tokens"
    CACHE_WRITE_INPUT_TEXT_TOKENS = "cache_write_input_text_tokens"
    OUTPUT_TEXT_TOKENS = "output_text_tokens"
    INPUT_AUDIO_TOKENS = "input_audio_tokens"
    CACHED_INPUT_AUDIO_TOKENS = "cached_input_audio_tokens"
    OUTPUT_AUDIO_TOKENS = "output_audio_tokens"
    AUDIO_INPUT_SECONDS = "audio_input_seconds"
    INPUT_CHARACTERS = "input_characters"
    CALL_SECONDS = "call_seconds"


class PriceUnit(StrEnum):
    USD_PER_1M_TOKENS = "usd_per_1m_tokens"
    USD_PER_MINUTE = "usd_per_minute"
    USD_PER_1M_CHARACTERS = "usd_per_1m_characters"

    @property
    def divisor(self) -> Decimal:
        """How many raw units (tokens / seconds / characters) one priced unit covers."""
        return {
            PriceUnit.USD_PER_1M_TOKENS: Decimal(1_000_000),
            PriceUnit.USD_PER_MINUTE: Decimal(60),
            PriceUnit.USD_PER_1M_CHARACTERS: Decimal(1_000_000),
        }[self]


class PriceComponent(BaseModel):
    dimension: BillingDimension
    unit: PriceUnit
    price_usd: Decimal


class PricingVersion(BaseModel):
    version: str
    effective_from: date
    components: list[PriceComponent]
    source_url: str | None = None
    verified: bool = Field(description="True only when copied from the provider's official pricing page")
    approximate: bool = Field(
        False,
        description="The provider bills on other dimensions; these components are its published approximation",
    )
    notes: str | None = None

    def component(self, dimension: BillingDimension) -> PriceComponent | None:
        return next((c for c in self.components if c.dimension == dimension), None)


class Capabilities(BaseModel):
    supports_streaming: bool = False
    supports_tool_calling: bool = False
    supports_audio_input: bool = False
    supports_audio_output: bool = False
    reports_usage: bool = Field(False, description="Provider returns consumption in its response")


class ModelDefinition(BaseModel):
    provider: ModelProvider
    model: str
    kind: ModelKind
    display_name: str
    capabilities: Capabilities
    context_window: int | None = None
    max_output_tokens: int | None = None
    deprecated: str | None = None
    pricing: list[PricingVersion] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    def current_pricing(self, on: date | None = None) -> PricingVersion | None:
        on = on or date.today()
        eligible = [p for p in self.pricing if p.effective_from <= on]
        return max(eligible, key=lambda p: p.effective_from, default=None)
