"""Lifecycle of one conversation session, shared by every audio transport.

A transport controller (Twilio media stream, browser WebSocket) only has to:
  runner = SessionRunner(); await runner.open(call_id, token, transport)
  ... await runner.feed(pcm) / await runner.playback_done(label) ...
  await runner.close(reason)
"""

import asyncio
import hmac
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np

from voice_lab.core.config import get_settings
from voice_lab.core.database import SessionLocal
from voice_lab.engines.base import AudioTransport, ConversationEngine, SessionContext
from voice_lab.engines.factory import create_engine
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import Call, ConversationSession
from voice_lab.usage.recorder import UsageRecorder

logger = logging.getLogger(__name__)


class SessionRunner:
    def __init__(self, sessionmaker=SessionLocal) -> None:  # type: ignore[no-untyped-def]
        self._sessionmaker = sessionmaker
        self.engine: ConversationEngine | None = None
        self.call_id: uuid.UUID | None = None
        self.session_id: uuid.UUID | None = None
        self.events: EventRecorder | None = None
        self.direction: str | None = None
        self._transport: AudioTransport | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self.hangup_reason: str | None = None

    async def open(self, call_id: str, token: str, transport: AudioTransport) -> bool:
        """Validate the single-use stream token, create the session and start the engine."""
        settings = get_settings()
        try:
            parsed_id = uuid.UUID(call_id)
        except (ValueError, TypeError):
            return False
        async with self._sessionmaker() as db:
            call = await db.get(Call, parsed_id)
            if call is None or not call.stream_token or not hmac.compare_digest(call.stream_token, token or ""):
                logger.warning("session rejected: bad call id or stream token")
                return False
            call.stream_token = None  # single use
            if call.direction == "browser":  # telephony calls get their status from provider webhooks
                call.status, call.answered_at = "in-progress", datetime.now(UTC)
            session = ConversationSession(call_id=call.id, engine=call.config_snapshot["engine"], status="active")
            db.add(session)
            await db.commit()
            self.call_id, self.session_id, self.direction = call.id, session.id, call.direction
            config = call.config_snapshot

        self._transport = transport
        self.events = EventRecorder(self._sessionmaker, self.call_id, self.session_id)
        if self.direction == "browser":
            await self.events.emit("call.answered", provider="browser")
        ctx = SessionContext(
            call_id=self.call_id,
            session_id=self.session_id,
            config=config,
            transport=transport,
            events=self.events,
            usage=UsageRecorder(self._sessionmaker, self.events),
            sessionmaker=self._sessionmaker,
            max_cost_usd=Decimal(settings.max_call_cost_usd),
        )
        try:
            self.engine = create_engine(ctx)
        except Exception as exc:
            await self.events.emit("session.failed", error=str(exc)[:300])
            await self.hangup("engine_unavailable")
            return True  # accepted, but already ending
        await self.engine.start()
        self._watchdog = asyncio.create_task(self._duration_watchdog(settings.max_call_duration_seconds))
        return True

    async def feed(self, samples: np.ndarray) -> None:
        if self.engine is not None:
            await self.engine.handle_audio(samples)

    async def playback_done(self, label: str) -> None:
        if self.engine is not None:
            await self.engine.on_playback_done(label)

    async def hangup(self, reason: str) -> None:
        self.hangup_reason = self.hangup_reason or reason
        if self._transport is not None:
            await self._transport.hangup(reason)

    async def close(self, reason: str) -> None:
        if self._watchdog:
            self._watchdog.cancel()
        # Shielded so a server shutdown mid-call still persists the session state.
        await asyncio.shield(self._finalize(self.hangup_reason or getattr(self._transport, "hung_up_reason", None) or reason))

    async def _finalize(self, reason: str) -> None:
        metrics = await self.engine.end(reason) if self.engine else {}
        if self.session_id is None:
            return
        now = datetime.now(UTC)
        async with self._sessionmaker() as db:
            session = await db.get(ConversationSession, self.session_id)
            if session:
                session.status, session.ended_at, session.metrics = "completed", now, metrics
            call = await db.get(Call, self.call_id)
            if call and call.direction == "browser" and call.ended_at is None:
                call.status, call.ended_at = "completed", now
                if call.answered_at:
                    call.duration_seconds = int((now - call.answered_at).total_seconds())
            await db.commit()
        if self.direction == "browser" and self.events:
            await self.events.emit("call.completed", provider="browser", reason=reason)

    async def _duration_watchdog(self, limit_s: int) -> None:
        """Backstop for the transport's own limits (e.g. Twilio time_limit)."""
        await asyncio.sleep(limit_s + 5)
        if self.events:
            await self.events.emit("call.duration_limit_reached", limit_s=limit_s)
        await self.hangup("duration_limit")
