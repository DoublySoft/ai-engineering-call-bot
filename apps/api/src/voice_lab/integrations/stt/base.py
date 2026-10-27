from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class Transcript:
    text: str
    audio_duration_ms: int
    # Raw usage block when the provider returns one (shape varies by model).
    provider_usage: dict | None = None


class STTCapabilities:
    streaming_partials: bool = False


class BatchSTT(Protocol):
    """Transcribes a complete utterance. Streaming STT providers (partials) will
    implement a separate StreamingSTT protocol; the pipeline checks capabilities."""

    provider: str
    capabilities: STTCapabilities

    async def transcribe(self, samples: np.ndarray, sample_rate: int, model: str, language: str | None) -> Transcript: ...
