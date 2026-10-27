import pytest

from voice_lab.core.config import get_settings
from voice_lab.modules.calls.schemas import OutboundCallRequest, SessionConfig
from voice_lab.modules.calls.service import CallError, check_outbound_prerequisites, pricing_snapshot, resolve_config


def test_defaults_are_filled_from_settings() -> None:
    cfg = resolve_config(SessionConfig(), get_settings())
    assert cfg["llm_provider"] == "openai" and cfg["llm_model"]
    assert cfg["stt_model"] and cfg["tts_model"] and cfg["tts_voice"]


@pytest.mark.parametrize(
    "override, message",
    [
        ({"engine": "realtime"}, "not implemented"),
        ({"voice_provider": "retell"}, "not implemented"),
        ({"llm_provider": "anthropic"}, "not implemented"),
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
