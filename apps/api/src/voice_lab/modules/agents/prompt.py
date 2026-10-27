from typing import Any

from voice_lab.modules.agents.schemas import LANGUAGES

# Fixed rules every voice agent gets, ahead of its own instructions. They exist
# because the reply is spoken, not because of the agent's role.
VOICE_RULES = (
    "Estás en una conversación por voz: tu respuesta se convertirá en audio. Responde con frases cortas y "
    "naturales (normalmente una o dos), sin listas, sin markdown, sin emojis y sin URLs. Si no entiendes lo "
    "que te dicen, pide que lo repitan. Idioma principal de la conversación: {language}; si la persona habla "
    "en otro idioma, responde en el suyo."
)
KNOWLEDGE_RULES = (
    "Usa esta información de referencia para responder. Si la respuesta no está aquí ni la sabes con "
    "seguridad, dilo en lugar de inventarla."
)


def compose_system_prompt(cfg: dict[str, Any]) -> str:
    parts = [
        VOICE_RULES.format(language=LANGUAGES.get(cfg.get("language", "es"), cfg.get("language", "es"))),
        "# Tu papel\n" + cfg["instructions"].strip(),
    ]
    knowledge = (cfg.get("knowledge") or "").strip()
    if knowledge:
        parts.append(f"# Información de referencia\n{KNOWLEDGE_RULES}\n\n{knowledge}")
    return "\n\n".join(parts)
