from collections.abc import AsyncIterator
from typing import Protocol

import numpy as np


class TTS(Protocol):
    """Streams PCM16 mono at `output_sample_rate`. Transports resample/encode."""

    provider: str
    output_sample_rate: int

    def synthesize(self, text: str, model: str, voice: str) -> AsyncIterator[np.ndarray]: ...
