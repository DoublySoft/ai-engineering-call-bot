"""Seed catalog. Prices were copied from the official pages on 2026-10-27.

Never add a price here without a source. Unknown prices stay empty and the
cost is recorded as unavailable; set them manually with PRICING_OVERRIDES_FILE.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from voice_lab.catalog.schemas import (
    BillingDimension as D,
)
from voice_lab.catalog.schemas import (
    Capabilities,
    ModelDefinition,
    ModelKind,
    ModelProvider,
    PriceComponent,
    PricingVersion,
)
from voice_lab.catalog.schemas import (
    PriceUnit as U,
)

OPENAI_PRICING_URL = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC_PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
VERIFIED_ON = date(2026, 10, 27)


def _openai_price(*components: tuple[D, U, str], notes: str | None = None, approximate: bool = False) -> list[PricingVersion]:
    return [
        PricingVersion(
            version=f"openai-{VERIFIED_ON.isoformat()}",
            effective_from=VERIFIED_ON,
            components=[PriceComponent(dimension=d, unit=u, price_usd=Decimal(p)) for d, u, p in components],
            source_url=OPENAI_PRICING_URL,
            verified=True,
            approximate=approximate,
            notes=notes,
        )
    ]


def _llm(model: str, name: str, inp: str, cached: str, out: str, **kw: object) -> ModelDefinition:
    return ModelDefinition(
        provider=ModelProvider.OPENAI,
        model=model,
        kind=ModelKind.LLM,
        display_name=name,
        capabilities=Capabilities(supports_streaming=True, supports_tool_calling=True, reports_usage=True),
        pricing=_openai_price(
            (D.INPUT_TEXT_TOKENS, U.USD_PER_1M_TOKENS, inp),
            (D.CACHED_INPUT_TEXT_TOKENS, U.USD_PER_1M_TOKENS, cached),
            (D.OUTPUT_TEXT_TOKENS, U.USD_PER_1M_TOKENS, out),
        ),
        **kw,  # type: ignore[arg-type]
    )


def _claude(model: str, name: str, inp: str, write_5m: str, read: str, out: str, notes: str | None = None) -> ModelDefinition:
    return ModelDefinition(
        provider=ModelProvider.ANTHROPIC,
        model=model,
        kind=ModelKind.LLM,
        display_name=name,
        capabilities=Capabilities(supports_streaming=True, supports_tool_calling=True, reports_usage=True),
        context_window=1_000_000,
        pricing=[
            PricingVersion(
                version=f"anthropic-{VERIFIED_ON.isoformat()}",
                effective_from=VERIFIED_ON,
                components=[
                    PriceComponent(dimension=D.INPUT_TEXT_TOKENS, unit=U.USD_PER_1M_TOKENS, price_usd=Decimal(inp)),
                    PriceComponent(dimension=D.CACHE_WRITE_INPUT_TEXT_TOKENS, unit=U.USD_PER_1M_TOKENS, price_usd=Decimal(write_5m)),
                    PriceComponent(dimension=D.CACHED_INPUT_TEXT_TOKENS, unit=U.USD_PER_1M_TOKENS, price_usd=Decimal(read)),
                    PriceComponent(dimension=D.OUTPUT_TEXT_TOKENS, unit=U.USD_PER_1M_TOKENS, price_usd=Decimal(out)),
                ],
                source_url=ANTHROPIC_PRICING_URL,
                verified=True,
                notes=notes,
            )
        ],
    )


CATALOG: list[ModelDefinition] = [
    # ---- LLMs (non-reasoning models first: lower time-to-first-token for voice)
    _llm("gpt-4.1-mini", "GPT-4.1 mini", "0.40", "0.10", "1.60"),
    _llm("gpt-4o-mini", "GPT-4o mini", "0.15", "0.075", "0.60"),
    _llm("gpt-5-mini", "GPT-5 mini (reasoning)", "0.25", "0.025", "2.00"),
    _llm("gpt-5.4-nano", "GPT-5.4 nano (reasoning)", "0.20", "0.02", "1.25"),
    # Anthropic, called with effort "low" (cache writes priced at the 5-minute TTL rate; thinking tokens bill as output)
    _claude(
        "claude-haiku-5-5",
        "Claude Haiku 5.5",
        "0.10",
        "0.125",
        "0.01",
        "0.50",
        notes="Prompts over 100,000 tokens bill at $0.50 / $2.50; voice turns stay far below that.",
    ),
    _claude("claude-sonnet-5-5", "Claude Sonnet 5.5", "2", "2.50", "0.10", "10"),
    _claude("claude-opus-5-5", "Claude Opus 5.5", "4", "5", "0.20", "20"),
    # ---- STT
    ModelDefinition(
        provider=ModelProvider.OPENAI,
        model="gpt-transcribe",
        kind=ModelKind.STT,
        display_name="GPT Transcribe",
        capabilities=Capabilities(supports_audio_input=True),
        pricing=_openai_price((D.AUDIO_INPUT_SECONDS, U.USD_PER_MINUTE, "0.0045")),
    ),
    ModelDefinition(
        provider=ModelProvider.OPENAI,
        model="gpt-4o-mini-transcribe",
        kind=ModelKind.STT,
        display_name="GPT-4o mini Transcribe",
        capabilities=Capabilities(supports_audio_input=True, reports_usage=True),
        deprecated="Shuts down 2027-02-26",
        pricing=_openai_price(
            (D.AUDIO_INPUT_SECONDS, U.USD_PER_MINUTE, "0.003"),
            notes="Per-minute figure is OpenAI's own estimate; token prices are $1.25 in / $5.00 out per 1M.",
            approximate=True,
        ),
    ),
    # ---- TTS
    ModelDefinition(
        provider=ModelProvider.OPENAI,
        model="tts-1",
        kind=ModelKind.TTS,
        display_name="TTS-1 (per character, exact cost)",
        capabilities=Capabilities(supports_streaming=True, supports_audio_output=True),
        deprecated="Shuts down 2027-01-06",
        pricing=_openai_price((D.INPUT_CHARACTERS, U.USD_PER_1M_CHARACTERS, "15.00")),
    ),
    ModelDefinition(
        provider=ModelProvider.OPENAI,
        model="gpt-4o-mini-tts",
        kind=ModelKind.TTS,
        display_name="GPT-4o mini TTS (token-billed, cost unavailable)",
        capabilities=Capabilities(supports_streaming=True, supports_audio_output=True),
        deprecated="Shuts down 2027-01-06",
        # Billed by text-in and audio-out tokens; /v1/audio/speech does not
        # return token counts, so the cost cannot be computed per request.
        pricing=_openai_price(
            (D.INPUT_TEXT_TOKENS, U.USD_PER_1M_TOKENS, "0.60"),
            (D.OUTPUT_AUDIO_TOKENS, U.USD_PER_1M_TOKENS, "12.00"),
        ),
    ),
    # ---- Speech-to-speech (OpenAI Realtime). Text and audio tokens are billed separately.
    *[
        ModelDefinition(
            provider=ModelProvider.OPENAI,
            model=model,
            kind=ModelKind.SPEECH_TO_SPEECH,
            display_name=name,
            capabilities=Capabilities(
                supports_streaming=True,
                supports_tool_calling=True,
                supports_audio_input=True,
                supports_audio_output=True,
                reports_usage=True,
            ),
            pricing=_openai_price(
                (D.INPUT_TEXT_TOKENS, U.USD_PER_1M_TOKENS, text_in),
                (D.CACHED_INPUT_TEXT_TOKENS, U.USD_PER_1M_TOKENS, text_cached),
                (D.OUTPUT_TEXT_TOKENS, U.USD_PER_1M_TOKENS, text_out),
                (D.INPUT_AUDIO_TOKENS, U.USD_PER_1M_TOKENS, audio_in),
                (D.CACHED_INPUT_AUDIO_TOKENS, U.USD_PER_1M_TOKENS, audio_cached),
                (D.OUTPUT_AUDIO_TOKENS, U.USD_PER_1M_TOKENS, audio_out),
            ),
        )
        for model, name, text_in, text_cached, text_out, audio_in, audio_cached, audio_out in (
            ("gpt-realtime-2.1-mini", "GPT Realtime 2.1 mini", "0.60", "0.06", "2.40", "10.00", "0.30", "20.00"),
            ("gpt-realtime-2.1", "GPT Realtime 2.1", "4.00", "0.40", "24.00", "32.00", "0.40", "64.00"),
        )
    ],
    # ---- Managed voice platform: Retell reports the combined cost per call.
    ModelDefinition(
        provider=ModelProvider.RETELL,
        model="retell-web-call",
        kind=ModelKind.MANAGED,
        display_name="Retell AI (managed agent)",
        capabilities=Capabilities(supports_audio_input=True, supports_audio_output=True, reports_usage=True),
    ),
    # ---- Telephony: Twilio reports the real price on the Call resource.
    ModelDefinition(
        provider=ModelProvider.TWILIO,
        model="programmable-voice",
        kind=ModelKind.TELEPHONY,
        display_name="Twilio Programmable Voice",
        capabilities=Capabilities(supports_audio_input=True, supports_audio_output=True, reports_usage=True),
    ),
]


def _apply_overrides(catalog: list[ModelDefinition]) -> list[ModelDefinition]:
    """PRICING_OVERRIDES_FILE: JSON list of {"key": "provider:model", ...PricingVersion}."""
    import os

    path = os.environ.get("PRICING_OVERRIDES_FILE")
    if not path or not Path(path).exists():
        return catalog
    by_key = {m.key: m for m in catalog}
    for raw in json.loads(Path(path).read_text()):
        model = by_key.get(raw.pop("key"))
        if model:
            model.pricing.append(PricingVersion.model_validate({"verified": False, **raw}))
    return catalog


_CATALOG = _apply_overrides(CATALOG)


def all_models() -> list[ModelDefinition]:
    return _CATALOG


def get_model(provider: str, model: str) -> ModelDefinition | None:
    return next((m for m in _CATALOG if m.provider == provider and m.model == model), None)


def models_of(kind: ModelKind) -> list[ModelDefinition]:
    return [m for m in _CATALOG if m.kind == kind]
