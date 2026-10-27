"""Deterministic stand-ins for providers. No network, no real calls."""

from collections.abc import AsyncIterator

import numpy as np

from voice_lab.integrations.llm.base import ChatMessage, LLMChunk, LLMUsage
from voice_lab.integrations.stt.base import STTCapabilities, Transcript


class FakeSTT:
    provider = "openai"
    capabilities = STTCapabilities()

    def __init__(self, text: str = "Hola, ¿qué tal?"):
        self.text = text
        self.calls = 0

    async def transcribe(self, samples: np.ndarray, sample_rate: int, model: str, language: str | None) -> Transcript:
        self.calls += 1
        return Transcript(text=self.text, audio_duration_ms=int(samples.size * 1000 / sample_rate))


class FakeLLM:
    provider = "openai"

    def __init__(self, reply: str = "Muy bien. ¿En qué puedo ayudarte?", usage: LLMUsage | None = None):
        self.reply = reply
        self.usage = usage or LLMUsage(input_tokens=900, cached_tokens=100, output_tokens=20)
        self.seen: list[list[ChatMessage]] = []

    async def stream(self, model: str, messages: list[ChatMessage], max_output_tokens: int) -> AsyncIterator[LLMChunk]:
        self.seen.append(list(messages))
        for word in self.reply.split(" "):
            yield LLMChunk(text=word + " ")
        yield LLMChunk(usage=self.usage)


class FakeTTS:
    provider = "openai"
    output_sample_rate = 24_000

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, model: str, voice: str) -> AsyncIterator[np.ndarray]:
        self.texts.append(text)
        yield np.full(2400, 1000, dtype=np.int16)  # 100 ms


class FakeTransport:
    name = "fake"
    input_sample_rate = 8000

    def __init__(self) -> None:
        self.audio_samples = 0
        self.marks: list[str] = []
        self.cleared = 0
        self.hangups: list[str] = []

    async def send_audio(self, samples: np.ndarray, sample_rate: int) -> None:
        self.audio_samples += samples.size

    async def flush_marker(self, label: str) -> None:
        self.marks.append(label)

    async def clear_audio(self) -> None:
        self.cleared += 1

    async def hangup(self, reason: str) -> None:
        self.hangups.append(reason)


def speech(ms: int, amplitude: int = 4000, rate: int = 8000) -> np.ndarray:
    t = np.arange(rate * ms // 1000)
    return (amplitude * np.sin(2 * np.pi * 220 * t / rate)).astype(np.int16)


def silence(ms: int, rate: int = 8000) -> np.ndarray:
    return np.zeros(rate * ms // 1000, dtype=np.int16)
