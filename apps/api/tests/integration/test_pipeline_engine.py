import asyncio
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import select

from tests.fakes import FakeLLM, FakeSTT, FakeTransport, FakeTTS, silence, speech
from voice_lab.core.config import get_settings
from voice_lab.core.database import SessionLocal
from voice_lab.engines.base import SessionContext
from voice_lab.engines.pipeline import PipelineEngine
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.calls.service import resolve_config
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import Call, CallEvent, ConversationMessage, ConversationSession, UsageEvent
from voice_lab.usage.recorder import UsageRecorder

pytestmark = pytest.mark.integration


async def _make_engine(max_cost: Decimal | None = None, **fakes):  # type: ignore[no-untyped-def]
    config = resolve_config(SessionConfig(greeting=None), get_settings())
    async with SessionLocal() as db:
        call = Call(direction="outbound", status="in-progress", config_snapshot=config)
        db.add(call)
        await db.flush()
        session = ConversationSession(call_id=call.id, engine="pipeline", status="active")
        db.add(session)
        await db.commit()
    events = EventRecorder(SessionLocal, call.id, session.id)
    transport = FakeTransport()
    ctx = SessionContext(
        call_id=call.id,
        session_id=session.id,
        config=config,
        transport=transport,
        events=events,
        usage=UsageRecorder(SessionLocal, events),
        sessionmaker=SessionLocal,
        max_cost_usd=max_cost,
    )
    engine = PipelineEngine(ctx, stt=fakes.get("stt", FakeSTT()), llm=fakes.get("llm", FakeLLM()), tts=fakes.get("tts", FakeTTS()))
    return engine, transport, call.id


async def _speak_turn(engine: PipelineEngine) -> None:
    for chunk in np.array_split(np.concatenate([silence(200), speech(600), silence(900)]), 85):  # ~20 ms chunks
        await engine.handle_audio(chunk)
    assert engine._turn_task is not None
    await engine._turn_task


async def test_full_turn_persists_transcript_usage_and_events() -> None:
    tts = FakeTTS()
    engine, transport, call_id = await _make_engine(tts=tts)
    await engine.start()
    await _speak_turn(engine)
    metrics = await engine.end("test")

    async with SessionLocal() as db:
        messages = (
            await db.scalars(
                select(ConversationMessage)
                .join(ConversationSession)
                .where(ConversationSession.call_id == call_id)
                .order_by(ConversationMessage.seq)
            )
        ).all()
        usage = (await db.scalars(select(UsageEvent).where(UsageEvent.call_id == call_id))).all()
        names = (await db.scalars(select(CallEvent.name).where(CallEvent.call_id == call_id))).all()

    assert [(m.role, m.text) for m in messages] == [("user", "Hola, ¿qué tal?"), ("agent", "Muy bien. ¿En qué puedo ayudarte?")]
    assert tts.texts == ["Muy bien.", "¿En qué puedo ayudarte?"]  # sentence-level streaming to TTS
    ops = {u.operation_type: u for u in usage if u.operation_type != "tts"}
    assert ops["llm"].input_tokens == 900 and ops["llm"].cached_tokens == 100 and ops["llm"].usage_source == "provider_reported"
    assert ops["llm"].cost_status == "calculated" and ops["llm"].cost_usd > 0
    assert ops["stt"].usage_source == "measured" and ops["stt"].audio_duration_ms > 0
    for expected in [
        "session.started",
        "speech.started",
        "speech.ended",
        "stt.completed",
        "llm.completed",
        "tts.completed",
        "usage.recorded",
        "audio.playback.started",
        "session.completed",
    ]:
        assert expected in names, expected
    assert transport.audio_samples > 0 and transport.marks
    assert metrics["turns"] == 1 and metrics["avg_time_to_first_audio_ms"] is not None


async def test_barge_in_clears_audio_and_cancels_turn() -> None:
    class SlowTTS(FakeTTS):
        async def synthesize(self, text, model, voice):  # type: ignore[no-untyped-def]
            await asyncio.sleep(5)
            yield np.zeros(10, np.int16)

    engine, transport, _ = await _make_engine(tts=SlowTTS())
    await engine.start()
    for chunk in np.array_split(np.concatenate([speech(600), silence(900)]), 75):
        await engine.handle_audio(chunk)
    await asyncio.sleep(0.2)  # turn is now stuck in TTS -> agent speaking
    assert engine.agent_speaking
    for chunk in np.array_split(speech(300), 15):
        await engine.handle_audio(chunk)
    assert transport.cleared == 1
    await engine.end("test")


async def test_spend_limit_hangs_up() -> None:
    engine, transport, _ = await _make_engine(max_cost=Decimal("0.000001"))
    await engine.start()
    await _speak_turn(engine)
    assert transport.hangups == ["spend_limit"]
    await engine.end("test")


async def test_empty_transcript_skips_llm() -> None:
    llm = FakeLLM()
    engine, _, _ = await _make_engine(stt=FakeSTT(text=""), llm=llm)
    await engine.start()
    await _speak_turn(engine)
    assert llm.seen == []
    await engine.end("test")
