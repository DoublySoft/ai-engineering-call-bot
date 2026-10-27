import pytest

from voice_lab.core.config import get_settings
from voice_lab.core.errors import LabError
from voice_lab.modules.agents.prompt import compose_system_prompt
from voice_lab.modules.agents.schemas import AgentSettings
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.sessions.config import resolve_config

AGENT = {
    "engine": "pipeline",
    "voice_provider": "custom",
    "instructions": "Eres la recepcionista de la Clínica Dental Sonrisa.",
    "knowledge": "Horario: lunes a viernes de 9 a 18 h.",
    "greeting": "Clínica Sonrisa, ¿dígame?",
    "language": "es",
    "llm_provider": "anthropic",
    "llm_model": "claude-haiku-5-5",
    "tts_voice": "nova",
}


def test_prompt_has_voice_rules_role_and_knowledge_in_order() -> None:
    prompt = compose_system_prompt({**AgentSettings().model_dump(), **AGENT})
    rules, role, knowledge = prompt.index("conversación por voz"), prompt.index("# Tu papel"), prompt.index("# Información de referencia")
    assert rules < role < knowledge
    assert "Clínica Dental Sonrisa" in prompt and "de 9 a 18 h" in prompt and "español" in prompt


def test_prompt_without_knowledge_has_no_reference_section() -> None:
    assert "Información de referencia" not in compose_system_prompt(AgentSettings().model_dump())


def test_agent_settings_are_the_base() -> None:
    cfg = resolve_config(SessionConfig(), get_settings(), AGENT)
    assert cfg["llm_model"] == "claude-haiku-5-5" and cfg["tts_voice"] == "nova" and cfg["greeting"] == "Clínica Sonrisa, ¿dígame?"
    assert cfg["stt_model"] == get_settings().stt_model  # unset on the agent -> server default
    assert "Clínica Dental Sonrisa" in cfg["system_prompt"]


def test_only_explicit_overrides_win() -> None:
    cfg = resolve_config(SessionConfig(llm_provider="openai", llm_model="gpt-4.1-mini"), get_settings(), AGENT)
    assert cfg["llm_model"] == "gpt-4.1-mini"
    assert cfg["tts_voice"] == "nova"  # not sent -> agent value kept


def test_greeting_none_override_means_silent_start() -> None:
    assert resolve_config(SessionConfig(greeting=None), get_settings(), AGENT)["greeting"] is None
    assert resolve_config(SessionConfig(), get_settings(), AGENT)["greeting"] == "Clínica Sonrisa, ¿dígame?"


@pytest.mark.parametrize("stored", ["ja", "nl", "Spanish", ""])
def test_legacy_stored_languages_still_load(stored: str) -> None:
    # Versions saved before agents existed could hold any language string.
    cfg = resolve_config(SessionConfig(), get_settings(), {**AGENT, "language": stored})
    assert cfg["language"] == (stored or "es")
    assert "# Tu papel" in cfg["system_prompt"]


@pytest.mark.parametrize("language, ok", [("ja", True), ("pt-BR", True), ("Spanish", False), ("es!", False)])
def test_new_versions_need_a_language_code(language: str, ok: bool) -> None:
    from pydantic import ValidationError

    from voice_lab.modules.agents.schemas import AgentWrite

    payload = {"name": "A", "settings": {"language": language}}
    if ok:
        assert AgentWrite.model_validate(payload).settings.language == language
    else:
        with pytest.raises(ValidationError):
            AgentWrite.model_validate(payload)


def test_unrunnable_agent_is_rejected() -> None:
    with pytest.raises(LabError, match="not a known llm"):
        resolve_config(SessionConfig(), get_settings(), {**AGENT, "llm_model": "claude-does-not-exist"})


def test_older_stored_settings_still_load() -> None:
    # Versions saved before agents were editable had system_prompt and no instructions.
    old = {"system_prompt": "x", "stt_model": "gpt-transcribe", "greeting": "Hola", "engine": "pipeline", "voice_provider": "custom"}
    cfg = resolve_config(SessionConfig(), get_settings(), old)
    assert cfg["greeting"] == "Hola" and "# Tu papel" in cfg["system_prompt"]
