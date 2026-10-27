"""Engine <-> transport contracts.

Telephony controllers only know `AudioTransport` and `ConversationEngine`;
adding an engine (Realtime, Retell-bridged, ...) never touches them.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voice_lab.observability.events import EventRecorder
from voice_lab.usage.recorder import UsageRecorder


class AudioTransport(Protocol):
    """Where the conversation's audio comes from and goes to (Twilio, browser)."""

    name: str
    input_sample_rate: int

    async def send_audio(self, samples: np.ndarray, sample_rate: int) -> None: ...

    async def flush_marker(self, label: str) -> None:
        """Ask to be told (via `on_playback_done`) once audio queued so far has played."""
        ...

    async def clear_audio(self) -> None:
        """Drop audio buffered on the far side (barge-in)."""
        ...

    async def hangup(self, reason: str) -> None: ...


@dataclass
class EngineCapabilities:
    streaming_stt: bool = False
    streaming_llm: bool = True
    streaming_tts: bool = True
    barge_in: bool = True
    turn_detection: str = "local_vad"  # local_vad | provider


@dataclass
class SessionContext:
    call_id: uuid.UUID
    session_id: uuid.UUID
    config: dict[str, Any]  # snapshot of the agent configuration for this call
    transport: AudioTransport
    events: EventRecorder
    usage: UsageRecorder
    sessionmaker: async_sessionmaker[AsyncSession]
    max_cost_usd: Any = None
    metrics: dict[str, Any] = field(default_factory=dict)


class ConversationEngine(Protocol):
    capabilities: EngineCapabilities

    async def start(self) -> None:
        """Begin the session (e.g. play the greeting)."""
        ...

    async def handle_audio(self, samples: np.ndarray) -> None:
        """Feed caller audio (PCM16 at transport.input_sample_rate). Must not block."""
        ...

    async def on_playback_done(self, label: str) -> None: ...

    async def end(self, reason: str) -> dict[str, Any]:
        """Stop work and return session metrics."""
        ...
