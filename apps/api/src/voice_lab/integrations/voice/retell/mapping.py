"""Translations from lab settings to Retell's own enums (checked 2026-10-27 against
docs.retellai.com create-retell-llm / create-agent)."""

# Our catalog LLM -> Retell `model_config.primary.model`. Retell runs and bills the LLM.
RETELL_LLM_MODELS = {
    "gpt-4.1-mini": "gpt-4.1-mini",
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5.4-nano": "gpt-5.4-nano",
    "claude-haiku-5-5": "claude-5.5-haiku",
    "claude-sonnet-5-5": "claude-5.5-sonnet",
    "claude-opus-5-5": "claude-5.5-opus",
}

# Our ISO 639 language -> Retell locale.
RETELL_LANGUAGES = {
    "es": "es-ES",
    "en": "en-US",
    "ca": "ca-ES",
    "fr": "fr-FR",
    "pt": "pt-PT",
    "it": "it-IT",
    "de": "de-DE",
    "nl": "nl-NL",
    "ja": "ja-JP",
}


def retell_language(code: str) -> str | None:
    if code in RETELL_LANGUAGES:
        return RETELL_LANGUAGES[code]
    return code if "-" in code else None  # already a locale such as pt-BR
