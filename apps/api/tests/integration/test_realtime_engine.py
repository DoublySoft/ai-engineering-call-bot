import asyncio
import base64
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select

from tests.fakes import FakeTransport
from voice_lab.core.config import get_settings
from voice_lab.core.database import SessionLocal
from voice_lab.engines.base import SessionContext
from voice_lab.engines.realtime import RealtimeEngine, usage_quantities
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.sessions.config import resolve_config
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import Call, CallEvent, ConversationMessage, ConversationSession, UsageEvent
from voice_lab.usage.recorder import UsageRecorder

pytestmark = pytest.mark.integration


def ev(type_: str, **fields):  # type: ignore[no-untyped-def]
    return SimpleNamespace(type=type_, **fields)


USAGE = {
    "input_tokens": 1200,
    "output_tokens": 300,
    "input_token_details": {
        "text_tokens": 1000,
        "audio_tokens": 200,
        "cached_tokens": 600,
        "cached_tokens_details": {"text_tokens": 600, "audio_tokens": 0},
    },
    "output_token_details": {"text_tokens": 60, "audio_tokens": 240},
}


class FakeConnection:
    """Records what the engine sends; replays scripted server events."""

    def __init__(self) -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self.sent: list[tuple[str, dict]] = []
        self.appended = 0
        outer = self

        class _NS:
            def __init__(self, prefix: str) -> None:
                self.prefix = prefix

            def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
                if name in ("item",):
                    return _NS(f"{self.prefix}.item")

                async def call(**kwargs):  # type: ignore[no-untyped-def]
                    if name == "append":
                        outer.appended += len(base64.b64decode(kwargs["audio"]))
                    else:
                        outer.sent.append((f"{self.prefix}.{name}", kwargs))

                return call

        self.session = _NS("session")
        self.input_audio_buffer = _NS("input_audio_buffer")
        self.response = _NS("response")
        self.conversation = _NS("conversation")

    async def recv(self):  # type: ignore[no-untyped-def]
        return await self.events.get()


class FakeManager:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.closed = False

    async def __aenter__(self) -> FakeConnection:
        return self.conn

    async def __aexit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        self.closed = True


async def _engine(greeting: str | None = "Hola, ¿qué tal?", max_cost: Decimal | None = None):  # type: ignore[no-untyped-def]
    config = resolve_config(SessionConfig(engine="realtime", greeting=greeting), get_settings())
    async with SessionLocal() as db:
        call = Call(direction="browser", status="in-progress", config_snapshot=config)
        db.add(call)
        await db.flush()
        session = ConversationSession(call_id=call.id, engine="realtime", status="active")
        db.add(session)
        await db.commit()
    events = EventRecorder(SessionLocal, call.id, session.id)
    transport = FakeTransport()
    transport.input_sample_rate = 16000
    ctx = SessionContext(call.id, session.id, config, transport, events, UsageRecorder(SessionLocal, events), SessionLocal, max_cost)
    conn = FakeConnection()
    manager = FakeManager(conn)
    engine = RealtimeEngine(ctx, connect=lambda model: manager)
    return engine, conn, manager, transport, call.id


def _audio(ms: int) -> str:
    return base64.b64encode(np.full(24 * ms, 500, dtype="<i2").tobytes()).decode()


async def _drain(conn: FakeConnection) -> None:
    for _ in range(100):
        if conn.events.empty():
            await asyncio.sleep(0.05)
            return
        await asyncio.sleep(0.01)


def test_usage_quantities_split_cached_text_and_audio() -> None:
    q = usage_quantities(USAGE)
    assert q[next(k for k in q if k.value == "input_text_tokens")] == 400
    assert q[next(k for k in q if k.value == "cached_input_text_tokens")] == 600
    assert q[next(k for k in q if k.value == "input_audio_tokens")] == 200
    assert q[next(k for k in q if k.value == "output_audio_tokens")] == 240


async def test_session_setup_greeting_turn_and_usage() -> None:
    engine, conn, manager, transport, call_id = await _engine()
    await engine.start()
    setup = conn.sent[0]
    assert setup[0] == "session.update"
    session = setup[1]["session"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["input"]["turn_detection"]["type"] == "server_vad"
    assert "# Tu papel" in session["instructions"]
    assert conn.sent[1][0] == "response.create"  # greeting

    # greeting response, then a stray cancelled response.done (seen live), then a user turn
    for e in [
        ev("response.created"),
        ev("response.output_audio.delta", delta=_audio(200), item_id="item_g"),
        ev("response.output_audio_transcript.done", transcript="«Hola, ¿qué tal?»"),
        ev("response.done", response={"status": "completed", "usage": USAGE}),
        ev("response.done", response={"status": "cancelled", "usage": None}),
        ev("input_audio_buffer.speech_started"),
        ev("input_audio_buffer.speech_stopped"),
        ev(
            "conversation.item.input_audio_transcription.completed",
            transcript="¿Abrís los sábados?",
            usage={"type": "duration", "seconds": 2.0},
        ),
        ev("response.created"),
        ev("response.output_audio.delta", delta=_audio(300), item_id="item_1"),
        ev("response.output_audio_transcript.delta", delta="Sí, de 10 a 14."),
        ev("response.output_audio_transcript.done", transcript="Sí, de 10 a 14."),
        ev("response.done", response={"status": "completed", "usage": USAGE}),
    ]:
        conn.events.put_nowait(e)
    await _drain(conn)

    await engine.handle_audio(np.zeros(1600, np.int16))  # 100 ms at 16 kHz -> resampled to 24 kHz
    assert conn.appended == 2400 * 2
    metrics = await engine.end("test")
    assert manager.closed

    async with SessionLocal() as db:
        msgs = (
            await db.scalars(
                select(ConversationMessage)
                .join(ConversationSession)
                .where(ConversationSession.call_id == call_id)
                .order_by(ConversationMessage.seq)
            )
        ).all()
        usage = (await db.scalars(select(UsageEvent).where(UsageEvent.call_id == call_id))).all()
    assert [(m.role, m.text) for m in msgs] == [("agent", "Hola, ¿qué tal?"), ("user", "¿Abrís los sábados?"), ("agent", "Sí, de 10 a 14.")]
    s2s = [u for u in usage if u.operation_type == "s2s"]
    assert len(s2s) == 2 and all(u.cost_status == "calculated" and u.cost_usd > 0 for u in s2s)
    stt = [u for u in usage if u.operation_type == "stt"]
    assert stt and stt[0].audio_duration_ms == 2000 and stt[0].cost_status == "calculated"
    assert transport.audio_samples == 24 * 500 and len(transport.marks) == 2
    assert metrics["turns"] == 1 and metrics["avg_time_to_first_audio_ms"] is not None


async def test_barge_in_clears_and_truncates() -> None:
    engine, conn, _, transport, call_id = await _engine(greeting=None)
    await engine.start()
    for e in [
        ev("input_audio_buffer.speech_stopped"),
        ev("response.created"),
        ev("response.output_audio.delta", delta=_audio(2000), item_id="item_x"),
        ev("input_audio_buffer.speech_started"),
        ev("response.done", response={"status": "cancelled", "usage": USAGE}),
    ]:
        conn.events.put_nowait(e)
    await _drain(conn)
    await engine.end("test")
    assert transport.cleared == 1
    truncate = [kw for name, kw in conn.sent if name == "conversation.item.truncate"]
    assert truncate and truncate[0]["item_id"] == "item_x" and 0 <= truncate[0]["audio_end_ms"] <= 2000
    async with SessionLocal() as db:
        names = (await db.scalars(select(CallEvent.name).where(CallEvent.call_id == call_id))).all()
    assert "agent.interrupted" in names


async def test_spend_limit_hangs_up() -> None:
    engine, conn, _, transport, _ = await _engine(greeting=None, max_cost=Decimal("0.000001"))
    await engine.start()
    for e in [ev("response.created"), ev("response.done", response={"status": "completed", "usage": USAGE})]:
        conn.events.put_nowait(e)
    await _drain(conn)
    await engine.end("test")
    assert transport.hangups == ["spend_limit"]


async def test_barge_in_after_generation_but_during_playback() -> None:
    engine, conn, _, transport, call_id = await _engine(greeting=None)
    await engine.start()
    for e in [
        ev("input_audio_buffer.speech_stopped"),
        ev("response.created"),
        ev("response.output_audio.delta", delta=_audio(3000), item_id="item_long"),
        ev("response.output_audio_transcript.done", transcript="Una respuesta larga que aún suena."),
        ev("response.done", response={"status": "completed", "usage": USAGE}),  # generation done, client still playing
        ev("input_audio_buffer.speech_started"),  # caller talks over it before the mark comes back
    ]:
        conn.events.put_nowait(e)
    await _drain(conn)
    assert transport.cleared == 1
    truncate = [kw for name, kw in conn.sent if name == "conversation.item.truncate"]
    assert truncate and truncate[0]["item_id"] == "item_long"
    # a playback mark arriving afterwards is harmless and doesn't re-trigger
    await engine.on_playback_done(transport.marks[-1])
    await engine.end("test")
    async with SessionLocal() as db:
        msg = await db.scalar(
            select(ConversationMessage)
            .join(ConversationSession)
            .where(ConversationSession.call_id == call_id, ConversationMessage.role == "agent")
        )
    assert msg is not None and msg.interrupted is True


async def test_no_barge_in_once_playback_finished() -> None:
    engine, conn, _, transport, _ = await _engine(greeting=None)
    await engine.start()
    for e in [
        ev("response.created"),
        ev("response.output_audio.delta", delta=_audio(200), item_id="item_s"),
        ev("response.done", response={"status": "completed", "usage": USAGE}),
    ]:
        conn.events.put_nowait(e)
    await _drain(conn)
    await engine.on_playback_done(transport.marks[-1])  # client finished playing
    conn.events.put_nowait(ev("input_audio_buffer.speech_started"))
    await _drain(conn)
    await engine.end("test")
    assert transport.cleared == 0


async def test_realtime_over_twilio_rates() -> None:
    """Twilio transport: 8 kHz in -> 24 kHz to OpenAI; 24 kHz out is handed to the transport,
    which resamples to 8 kHz mu-law itself."""
    engine, conn, _, transport, _ = await _engine(greeting=None)
    transport.input_sample_rate = 8000
    await engine.start()
    await engine.handle_audio(np.zeros(160, np.int16))  # 20 ms Twilio frame
    assert conn.appended == 480 * 2
    await engine.end("test")
