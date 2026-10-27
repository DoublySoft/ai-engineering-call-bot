from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from voice_lab.integrations.llm.anthropic_llm import AnthropicLLM, LLMRefused, to_anthropic_messages
from voice_lab.integrations.llm.base import ChatMessage


class FakeStream:
    def __init__(self, texts: list[str], final: SimpleNamespace):
        self._texts, self._final = texts, final

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        return None

    @property
    def text_stream(self):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            for t in self._texts:
                yield t

        return gen()

    async def get_final_message(self) -> SimpleNamespace:
        return self._final


def _final(stop_reason: str = "end_turn", model: str = "claude-sonnet-5-5", iterations: list | None = None) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=50, cache_read_input_tokens=10, cache_creation_input_tokens=0, output_tokens=7, iterations=iterations
    )
    return SimpleNamespace(stop_reason=stop_reason, model=model, usage=usage, stop_details=SimpleNamespace(category="cyber"))


def _llm(stream: FakeStream) -> tuple[AnthropicLLM, MagicMock]:
    client = MagicMock()
    client.beta.messages.stream = MagicMock(return_value=stream)
    return AnthropicLLM(client), client.beta.messages.stream


HISTORY = [ChatMessage("system", "Be brief."), ChatMessage("assistant", "Hola."), ChatMessage("user", "¿Qué tal?")]


def test_greeting_first_history_starts_with_user() -> None:
    system, convo = to_anthropic_messages(HISTORY)
    assert system == "Be brief."
    assert [m["role"] for m in convo] == ["user", "assistant", "user"]


async def test_stream_yields_text_then_usage_with_cache_split() -> None:
    llm, stream = _llm(FakeStream(["Muy ", "bien."], _final()))
    chunks = [c async for c in llm.stream("claude-sonnet-5-5", HISTORY, 300)]
    assert "".join(c.text for c in chunks) == "Muy bien."
    usage = chunks[-1].usage
    assert usage and usage.input_tokens == 50 and usage.cached_tokens == 10 and usage.cache_write_tokens == 0
    assert usage.served_by_model is None
    kwargs = stream.call_args.kwargs
    assert "thinking" not in kwargs and kwargs["output_config"] == {"effort": "low"}
    assert kwargs["fallbacks"] == "default" and kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["max_tokens"] > 300  # thinking headroom


@pytest.mark.parametrize("model, fallbacks", [("claude-haiku-5-5", False), ("claude-opus-5-5", True)])
async def test_per_model_request_shape(model: str, fallbacks: bool) -> None:
    llm, stream = _llm(FakeStream(["ok"], _final(model=model)))
    _ = [c async for c in llm.stream(model, HISTORY, 100)]
    kwargs = stream.call_args.kwargs
    assert "thinking" not in kwargs  # adaptive default measured fastest for voice
    assert ("fallbacks" in kwargs) is fallbacks


async def test_refusal_raises() -> None:
    llm, _ = _llm(FakeStream([], _final(stop_reason="refusal")))
    with pytest.raises(LLMRefused, match="cyber"):
        _ = [c async for c in llm.stream("claude-sonnet-5-5", HISTORY, 100)]


async def test_fallback_reports_serving_model() -> None:
    final = _final(model="claude-opus-4-8", iterations=[SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")])
    llm, _ = _llm(FakeStream(["ok"], final))
    chunks = [c async for c in llm.stream("claude-opus-5-5", HISTORY, 100)]
    assert chunks[-1].usage and chunks[-1].usage.served_by_model == "claude-opus-4-8"
