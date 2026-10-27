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
from voice_lab.integrations.llm.base import LLMChunk
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.sessions.config import resolve_config
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


async def test_llm_timing_excludes_tts() -> None:
    class SlowTTS(FakeTTS):
        async def synthesize(self, text, model, voice):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.3)
            yield np.zeros(240, np.int16)

    engine, _, call_id = await _make_engine(tts=SlowTTS())
    await engine.start()
    await _speak_turn(engine)
    await engine.end("test")
    async with SessionLocal() as db:
        llm_ms = await db.scalar(select(CallEvent.duration_ms).where(CallEvent.call_id == call_id, CallEvent.name == "llm.completed"))
        tts_ms = (
            await db.scalars(select(CallEvent.duration_ms).where(CallEvent.call_id == call_id, CallEvent.name == "tts.completed"))
        ).all()
    assert len(tts_ms) == 2 and all(ms >= 300 for ms in tts_ms)
    assert llm_ms is not None and llm_ms < 200  # two sentences of slow TTS would be >= 600 ms


async def test_llm_failure_still_unblocks_turn() -> None:
    class BrokenLLM(FakeLLM):
        async def stream(self, model, messages, max_output_tokens):  # type: ignore[no-untyped-def]
            yield LLMChunk(text="Hola. ")
            raise RuntimeError("upstream 500")

    engine, _, call_id = await _make_engine(llm=BrokenLLM())
    await engine.start()
    await _speak_turn(engine)
    await engine.end("test")
    async with SessionLocal() as db:
        names = (await db.scalars(select(CallEvent.name).where(CallEvent.call_id == call_id))).all()
        msg = await db.scalar(select(ConversationMessage).join(ConversationSession).where(ConversationSession.call_id == call_id))
    assert "llm.failed" in names and "turn.failed" in names
    assert msg is not None and msg.role == "user"


async def test_refused_llm_turn_still_records_usage() -> None:
    from voice_lab.integrations.llm.base import LLMUsage

    class RefusingLLM(FakeLLM):
        async def stream(self, model, messages, max_output_tokens):  # type: ignore[no-untyped-def]
            yield LLMChunk(usage=LLMUsage(input_tokens=40, cached_tokens=0, output_tokens=3))
            raise RuntimeError("Claude declined the request (category: cyber)")

    engine, _, call_id = await _make_engine(llm=RefusingLLM())
    await engine.start()
    await _speak_turn(engine)
    await engine.end("test")
    async with SessionLocal() as db:
        llm_usage = await db.scalar(select(UsageEvent).where(UsageEvent.call_id == call_id, UsageEvent.operation_type == "llm"))
        names = (await db.scalars(select(CallEvent.name).where(CallEvent.call_id == call_id))).all()
    assert llm_usage is not None and llm_usage.input_tokens == 40 and llm_usage.cost_status == "calculated"
    assert "turn.failed" in names
