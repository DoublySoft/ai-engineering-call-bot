import base64
import json

import numpy as np
from fastapi import WebSocket

from voice_lab.audio.codec import pcm16_to_mulaw, resample
from voice_lab.integrations.telephony.twilio.client import TwilioTelephony

TWILIO_RATE = 8000
FRAME_BYTES = 160  # 20 ms of 8 kHz mu-law


class TwilioMediaTransport:
    """AudioTransport over a bidirectional Twilio Media Stream (mu-law/8 kHz)."""

    name = "twilio"
    input_sample_rate = TWILIO_RATE

    def __init__(self, ws: WebSocket, stream_sid: str, call_sid: str, telephony: TwilioTelephony | None):
        self._ws = ws
        self._stream_sid = stream_sid
        self._call_sid = call_sid
        self._telephony = telephony
        self.hung_up_reason: str | None = None

    async def _send(self, message: dict) -> None:
        await self._ws.send_text(json.dumps({"streamSid": self._stream_sid, **message}))

    async def send_audio(self, samples: np.ndarray, sample_rate: int) -> None:
        payload = pcm16_to_mulaw(resample(samples, sample_rate, TWILIO_RATE))
        for i in range(0, len(payload), FRAME_BYTES):
            chunk = base64.b64encode(payload[i : i + FRAME_BYTES]).decode()
            await self._send({"event": "media", "media": {"payload": chunk}})

    async def flush_marker(self, label: str) -> None:
        await self._send({"event": "mark", "mark": {"name": label}})

    async def clear_audio(self) -> None:
        await self._send({"event": "clear"})

    async def hangup(self, reason: str) -> None:
        self.hung_up_reason = reason
        if self._telephony:
            await self._telephony.hangup(self._call_sid)
