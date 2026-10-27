"""Hits the real Anthropic API with ANTHROPIC_API_KEY from .env. Costs well under a cent.

Run with: uv run pytest -m manual tests/manual -s
"""

import time

import pytest

from voice_lab.integrations.llm.anthropic_llm import AnthropicLLM
from voice_lab.integrations.llm.base import ChatMessage

pytestmark = pytest.mark.manual


@pytest.fixture
def llm():  # type: ignore[no-untyped-def]
    from anthropic import AsyncAnthropic

    from tests.conftest import REAL_KEYS

    if not REAL_KEYS["anthropic"]:
        pytest.skip("ANTHROPIC_API_KEY not configured in .env")
    return AnthropicLLM(AsyncAnthropic(api_key=REAL_KEYS["anthropic"]))


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"])
async def test_stream_with_greeting_first(llm, model: str) -> None:  # type: ignore[no-untyped-def]
    history = [
        ChatMessage("system", "Eres un asistente de voz. Responde en una frase corta."),
        ChatMessage("assistant", "Hola, ¿en qué puedo ayudarte?"),  # agent speaks first, like a call
        ChatMessage("user", "¿Cuál es la capital de Francia?"),
    ]
    t0 = time.perf_counter()
    ttft = None
    text, usage = "", None
    async for chunk in llm.stream(model, history, 200):
        if chunk.text and ttft is None:
            ttft = time.perf_counter() - t0
        text += chunk.text
        usage = chunk.usage or usage
    print(f"\n{model}: ttft={ttft and round(ttft * 1000)}ms total={round((time.perf_counter() - t0) * 1000)}ms {usage} -> {text!r}")
    assert "par" in text.lower()
    assert usage and usage.input_tokens and usage.output_tokens and usage.cache_write_tokens is not None
