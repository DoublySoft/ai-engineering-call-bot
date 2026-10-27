import json

import numpy as np
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from voice_lab.audio.codec import resample
from voice_lab.core.websocket import close_quietly

BROWSER_INPUT_RATE = 16_000  # what the mic worklet sends
BROWSER_OUTPUT_RATE = 24_000  # what the page plays (OpenAI TTS native rate)


class BrowserTransport:
    """AudioTransport over a browser WebSocket.

    Binary frames carry raw PCM16 little-endian mono (in: 16 kHz, out: 24 kHz).
    Text frames carry JSON control messages: mark / clear / hangup (server -> page)
    and mark / stop (page -> server).
    """

    name = "browser"
    input_sample_rate = BROWSER_INPUT_RATE

    def __init__(self, ws: WebSocket):
        self._ws = ws
        self.hung_up_reason: str | None = None

    @property
    def _open(self) -> bool:
        return self._ws.application_state == WebSocketState.CONNECTED

    async def send_audio(self, samples: np.ndarray, sample_rate: int) -> None:
        if self._open:
            await self._ws.send_bytes(resample(samples, sample_rate, BROWSER_OUTPUT_RATE).astype("<i2").tobytes())

    async def flush_marker(self, label: str) -> None:
        if self._open:
            await self._ws.send_text(json.dumps({"type": "mark", "name": label}))

    async def clear_audio(self) -> None:
        if self._open:
            await self._ws.send_text(json.dumps({"type": "clear"}))

    async def hangup(self, reason: str) -> None:
        self.hung_up_reason = reason
        if self._open:
            await self._ws.send_text(json.dumps({"type": "hangup", "reason": reason}))
            await close_quietly(self._ws)
