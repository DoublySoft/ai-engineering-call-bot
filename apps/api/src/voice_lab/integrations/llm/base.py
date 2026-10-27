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
    cached_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class LLMChunk:
    """A stream item: text deltas, then a final chunk carrying usage."""

    text: str = ""
    usage: LLMUsage | None = None


class LLMClient(Protocol):
    provider: str

    def stream(self, model: str, messages: list[ChatMessage], max_output_tokens: int) -> AsyncIterator[LLMChunk]: ...
