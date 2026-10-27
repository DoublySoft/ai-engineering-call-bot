from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from voice_lab.integrations.llm.base import ChatMessage, LLMChunk, LLMUsage


class OpenAILLM:
    provider = "openai"

    def __init__(self, client: AsyncOpenAI):
        self._client = client

    async def stream(self, model: str, messages: list[ChatMessage], max_output_tokens: int) -> AsyncIterator[LLMChunk]:
        kwargs: dict = {}
        if model.startswith("gpt-5"):
            # Reasoning models: keep thinking minimal, it dominates voice latency.
            kwargs["reasoning_effort"] = "minimal"
        stream = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_completion_tokens=max_output_tokens,
            stream=True,
            stream_options={"include_usage": True},
            **kwargs,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield LLMChunk(text=chunk.choices[0].delta.content)
            if chunk.usage:
                cached = 0
                if chunk.usage.prompt_tokens_details and chunk.usage.prompt_tokens_details.cached_tokens:
                    cached = chunk.usage.prompt_tokens_details.cached_tokens
                yield LLMChunk(
                    usage=LLMUsage(
                        input_tokens=chunk.usage.prompt_tokens - cached,
                        cached_tokens=cached,
                        output_tokens=chunk.usage.completion_tokens,
                    )
                )
