import pytest

from voice_lab.core.config import get_settings
from voice_lab.core.errors import LabError as CallError
from voice_lab.modules.calls.schemas import OutboundCallRequest, SessionConfig
from voice_lab.modules.calls.service import check_outbound_prerequisites
from voice_lab.modules.sessions.config import pricing_snapshot, resolve_config


def test_defaults_are_filled_from_settings() -> None:
    cfg = resolve_config(SessionConfig(), get_settings())
    assert cfg["llm_provider"] == "openai" and cfg["llm_model"]
    assert cfg["stt_model"] and cfg["tts_model"] and cfg["tts_voice"]


@pytest.mark.parametrize(
    "override, message",
    [
        ({"engine": "realtime", "s2s_model": "tts-1"}, "not a known speech_to_speech"),
        ({"voice_provider": "retell", "llm_model": "gpt-4o-mini"}, "Retell can't run"),
        ({"llm_provider": "openai", "llm_model": "claude-sonnet-5-5"}, "not a known llm"),
        ({"llm_model": "gpt-transcribe"}, "not a known llm"),
        ({"tts_model": "made-up-tts"}, "not a known tts"),
    ],
)
def test_incompatible_combinations_are_rejected(override: dict, message: str) -> None:
    with pytest.raises(CallError, match=message):
        resolve_config(SessionConfig(**override), get_settings())


def test_pricing_snapshot_captures_versions() -> None:
    snap = pricing_snapshot(resolve_config(SessionConfig(), get_settings()))
    assert snap["llm"]["verified"] is True and snap["llm"]["source_url"].startswith("https://")


def test_outbound_requires_allowlisted_number() -> None:
    with pytest.raises(CallError) as err:
        check_outbound_prerequisites(get_settings(), "+34600999999")
    assert err.value.status_code == 403
    check_outbound_prerequisites(get_settings(), "+34600111222")


def test_phone_numbers_are_normalized_and_validated() -> None:
    assert OutboundCallRequest(to="+34 600-111-222").to == "+34600111222"
    with pytest.raises(ValueError):
        OutboundCallRequest(to="600111222")


def test_anthropic_models_are_selectable() -> None:
    cfg = resolve_config(SessionConfig(llm_provider="anthropic", llm_model="claude-haiku-5-5"), get_settings())
    assert cfg["llm_provider"] == "anthropic" and cfg["llm_model"] == "claude-haiku-5-5"


def test_realtime_validates_only_its_models() -> None:
    cfg = resolve_config(SessionConfig(engine="realtime"), get_settings())
    assert cfg["s2s_model"] == get_settings().s2s_model
    snap = pricing_snapshot(cfg)
    assert set(snap) == {"s2s", "stt"} and snap["s2s"]["verified"] is True


def test_retell_maps_llm_and_skips_pipeline_pricing() -> None:
    cfg = resolve_config(SessionConfig(voice_provider="retell", llm_provider="anthropic", llm_model="claude-haiku-5-5"), get_settings())
    assert cfg["retell_voice_id"] and pricing_snapshot(cfg) == {"retell": "provider_reported"}
