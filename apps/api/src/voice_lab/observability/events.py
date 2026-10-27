"""Structured call events: persisted (CallEvent), logged as JSON and pushed live.

Event names follow the lab taxonomy (call.created, stt.completed, ...).
`correlation_id` ties together the events of one conversational turn.
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voice_lab.core.logging import log
from voice_lab.observability.hub import hub
from voice_lab.persistence.models import CallEvent

logger = logging.getLogger("voice_lab.events")


class EventRecorder:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], call_id: uuid.UUID, session_id: uuid.UUID | None = None):
        self._sessionmaker = sessionmaker
        self.call_id = call_id
        self.session_id = session_id

    async def emit(
        self,
        name: str,
        *,
        correlation_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        duration_ms: int | None = None,
        **data: Any,
    ) -> None:
        now = datetime.now(UTC)
        async with self._sessionmaker() as db:
            db.add(
                CallEvent(
                    call_id=self.call_id,
                    session_id=self.session_id,
                    correlation_id=correlation_id,
                    name=name,
                    provider=provider,
                    model=model,
                    duration_ms=duration_ms,
                    data=data,
                    timestamp=now,
                )
            )
            await db.commit()
        message = {
            "type": "event",
            "name": name,
            "call_id": str(self.call_id),
            "session_id": str(self.session_id) if self.session_id else None,
            "correlation_id": correlation_id,
            "provider": provider,
            "model": model,
            "duration_ms": duration_ms,
            "data": data,
            "timestamp": now.isoformat(),
        }
        log(logger, name, **{k: v for k, v in message.items() if k != "type"})
        hub.publish(str(self.call_id), message)

    def push(self, message: dict[str, Any]) -> None:
        """Live-only message (e.g. partial transcripts) that is not persisted as an event."""
        hub.publish(str(self.call_id), {"call_id": str(self.call_id), **message})

    @asynccontextmanager
    async def span(self, prefix: str, *, correlation_id: str, provider: str, model: str, **data: Any) -> AsyncIterator[dict[str, Any]]:
        """Emits `<prefix>.started` / `<prefix>.completed` (or `.failed`) with measured duration.
        The yielded dict lets the caller attach result metadata to the completion event."""
        await self.emit(f"{prefix}.started", correlation_id=correlation_id, provider=provider, model=model, **data)
        result: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            yield result
        except Exception as exc:
            await self.emit(
                f"{prefix}.failed",
                correlation_id=correlation_id,
                provider=provider,
                model=model,
                duration_ms=int((time.perf_counter() - start) * 1000),
                error=type(exc).__name__,
            )
            raise
        await self.emit(
            f"{prefix}.completed",
            correlation_id=correlation_id,
            provider=provider,
            model=model,
            duration_ms=int((time.perf_counter() - start) * 1000),
            **result,
        )
