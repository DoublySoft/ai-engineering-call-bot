"""Energy-based voice activity / end-of-turn detector.

Deliberately simple and dependency-free. It adapts to the line's noise floor
and reports `speech_started` / `speech_ended` per 20 ms frame. Providers with
their own turn detection (Realtime, Retell) bypass this entirely.
"""

from dataclasses import dataclass, field
from enum import Enum

import numpy as np


class VadEvent(Enum):
    NONE = 0
    SPEECH_STARTED = 1
    SPEECH_ENDED = 2


@dataclass
class EnergyVad:
    sample_rate: int = 8000
    frame_ms: int = 20
    start_frames: int = 3  # 60 ms above threshold -> speech
    end_silence_ms: int = 700  # pause that closes a turn
    min_rms: float = 350.0
    noise_ratio: float = 3.0
    _noise: float = 200.0
    _speech_run: int = 0
    _silence_run: int = 0
    in_speech: bool = False
    _buffer: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16))

    @property
    def frame_size(self) -> int:
        return self.sample_rate * self.frame_ms // 1000

    def process(self, samples: np.ndarray) -> list[tuple[VadEvent, np.ndarray]]:
        """Feed any amount of PCM16; returns (event, frame) per complete frame."""
        self._buffer = np.concatenate([self._buffer, samples])
        out: list[tuple[VadEvent, np.ndarray]] = []
        while self._buffer.size >= self.frame_size:
            frame, self._buffer = self._buffer[: self.frame_size], self._buffer[self.frame_size :]
            out.append((self._step(frame), frame))
        return out

    def _step(self, frame: np.ndarray) -> VadEvent:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        threshold = max(self.min_rms, self._noise * self.noise_ratio)
        voiced = rms > threshold
        if not voiced and not self.in_speech:
            self._noise = 0.95 * self._noise + 0.05 * rms
        if voiced:
            self._speech_run += 1
            self._silence_run = 0
            if not self.in_speech and self._speech_run >= self.start_frames:
                self.in_speech = True
                return VadEvent.SPEECH_STARTED
        else:
            self._speech_run = 0
            if self.in_speech:
                self._silence_run += 1
                if self._silence_run * self.frame_ms >= self.end_silence_ms:
                    self.in_speech = False
                    self._silence_run = 0
                    return VadEvent.SPEECH_ENDED
        return VadEvent.NONE
