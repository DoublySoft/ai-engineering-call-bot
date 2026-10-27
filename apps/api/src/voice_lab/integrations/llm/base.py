from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class LLMUsage:
    input_tokens: int | None = None  # uncached input tokens
    cached_tokens: int | None = None  # cache reads
    output_tokens: int | None = None  # includes reasoning tokens where the provider bills them as output
    cache_write_tokens: int | None = None  # None: provider has no separate cache-write charge
    served_by_model: str | None = None  # set when a provider-side fallback answered instead


@dataclass
class LLMChunk:
    """A stream item: text deltas, then a final chunk carrying usage."""

    text: str = ""
    usage: LLMUsage | None = None


class LLMClient(Protocol):
    provider: str

    def stream(self, model: str, messages: list[ChatMessage], max_output_tokens: int) -> AsyncIterator[LLMChunk]: ...
