"""Turns "agent version + per-call overrides + server defaults" into the config
snapshot a session runs with (and stores, so history survives later edits)."""

from typing import Any

from voice_lab.catalog.registry import get_model
from voice_lab.catalog.schemas import ModelKind
from voice_lab.core.config import Settings
from voice_lab.core.errors import LabError
from voice_lab.integrations.registry import IMPLEMENTED
from voice_lab.modules.agents.prompt import compose_system_prompt
from voice_lab.modules.agents.schemas import AgentSettings
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.persistence.models import VoiceAgentConfiguration

_MODEL_PARTS = (("stt", ModelKind.STT), ("llm", ModelKind.LLM), ("tts", ModelKind.TTS))


class ConfigError(LabError):
    pass


def base_from_configuration(cfg: VoiceAgentConfiguration) -> dict[str, Any]:
    return {"engine": cfg.engine, "voice_provider": cfg.voice_provider, **cfg.settings}


def resolve_config(overrides: SessionConfig, settings: Settings, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge agent settings (base), explicit per-call overrides and .env defaults, then
    reject combinations the lab can't run. Adds the composed `system_prompt`."""
    base = base or {}
    resolved: dict[str, Any] = {
        "engine": base.get("engine", "pipeline"),
        "voice_provider": base.get("voice_provider", "custom"),
        **AgentSettings.model_validate(base).model_dump(),
    }
    for field in overrides.model_fields_set - {"agent_id"}:
        value = getattr(overrides, field)
        if value is not None or field == "greeting":  # greeting=None means "start silent"
            resolved[field] = value
    for part in ("stt", "llm", "tts"):
        resolved[f"{part}_provider"] = resolved.get(f"{part}_provider") or getattr(settings, f"{part}_provider")
        resolved[f"{part}_model"] = resolved.get(f"{part}_model") or getattr(settings, f"{part}_model")
    resolved["tts_voice"] = resolved.get("tts_voice") or settings.tts_voice
    resolved["language"] = resolved.get("language") or settings.agent_language

    if resolved["voice_provider"] not in IMPLEMENTED["voice_provider"]:
        raise ConfigError(f"voice provider '{resolved['voice_provider']}' is not implemented yet (planned: Phase 7)")
    if resolved["engine"] not in IMPLEMENTED["engine"]:
        raise ConfigError(f"engine '{resolved['engine']}' is not implemented yet (planned: Phase 6)")
    for part, kind in _MODEL_PARTS:
        provider, model = resolved[f"{part}_provider"], resolved[f"{part}_model"]
        if provider not in IMPLEMENTED[part]:
            raise ConfigError(f"{part.upper()} provider '{provider}' is not implemented yet")
        definition = get_model(provider, model)
        if definition is None or definition.kind != kind:
            raise ConfigError(f"'{provider}:{model}' is not a known {kind.value} model in the catalog")

    resolved["system_prompt"] = compose_system_prompt(resolved)
    return resolved


def pricing_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for part, _ in _MODEL_PARTS:
        definition = get_model(config[f"{part}_provider"], config[f"{part}_model"])
        pricing = definition.current_pricing() if definition else None
        snapshot[part] = pricing.model_dump(mode="json") if pricing else None
    return snapshot


def check_provider_keys(config: dict[str, Any], settings: Settings) -> None:
    """Fail before dialing/connecting if a selected provider has no API key."""
    keys = {"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key}
    for part, _ in _MODEL_PARTS:
        provider = config[f"{part}_provider"]
        if provider in keys and not keys[provider]:
            raise ConfigError(f"{provider.upper()}_API_KEY is not set (needed for {part.upper()})", 503)
