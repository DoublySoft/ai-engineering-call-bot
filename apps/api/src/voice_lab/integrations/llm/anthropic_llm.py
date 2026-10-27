"""Claude via the official Anthropic SDK (Messages API, streaming).

Voice needs low time-to-first-token. Measured on 2026-10-27 (docs/architecture.md):
leaving thinking at its adaptive default with effort "low" gives the lowest TTFT on
Haiku/Sonnet/Opus 5.5. Turning thinking off ("disabled" on Haiku, "between_tools" on
Sonnet) was 1.5-5x *slower* to first token, so the adapter never sends `thinking`.
Opus/Sonnet 5.5 also opt into server-side refusal fallbacks ("default" mode):
a declined request is re-run on Anthropic's recommended fallback model.
"""

from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

from voice_lab.integrations.llm.base import ChatMessage, LLMChunk, LLMUsage

# Thinking tokens count against max_tokens; keep room so a short spoken reply
# is never cut off by reasoning. Reply length is governed by the system prompt.
THINKING_HEADROOM_TOKENS = 4096
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_SUPPORTS_FALLBACKS = {"claude-opus-5-5", "claude-sonnet-5-5"}


class LLMRefused(RuntimeError):
    """Claude (and any fallback) declined the request: stop_reason == "refusal"."""


def to_anthropic_messages(messages: list[ChatMessage]) -> tuple[str, list[dict[str, str]]]:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    convo = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
    # The first message must be from the user; our greeting is spoken by the agent first.
    if convo and convo[0]["role"] == "assistant":
        convo.insert(0, {"role": "user", "content": "[call connected]"})
    return system, convo


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, client: AsyncAnthropic):
        self._client = client

    async def stream(self, model: str, messages: list[ChatMessage], max_output_tokens: int) -> AsyncIterator[LLMChunk]:
        system, convo = to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens + THINKING_HEADROOM_TOKENS,
            "system": system,
            "messages": convo,
            "output_config": {"effort": "low"},
            # Automatic prompt caching: the system prompt (voice rules + role + knowledge)
            # and earlier turns are re-read at ~10% of the input price on later turns.
            # Prefixes below the model's minimum are simply not cached.
            "cache_control": {"type": "ephemeral"},
        }
        if model in _SUPPORTS_FALLBACKS:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        async with self._client.beta.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield LLMChunk(text=text)
            final = await stream.get_final_message()

        if final.stop_reason == "refusal":
            category = final.stop_details.category if final.stop_details else None
            raise LLMRefused(f"Claude declined the request (category: {category})")

        usage = final.usage
        fell_back = any(getattr(it, "type", None) == "fallback_message" for it in (usage.iterations or []))
        yield LLMChunk(
            usage=LLMUsage(
                input_tokens=usage.input_tokens,
                cached_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
                output_tokens=usage.output_tokens,
                served_by_model=final.model if fell_back else None,
            )
        )
