from datetime import UTC, datetime
from decimal import Decimal

import pytest

from voice_lab.core.config import get_settings
from voice_lab.core.database import SessionLocal
from voice_lab.modules.analytics.service import analytics, architecture_of
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.sessions.config import resolve_config
from voice_lab.persistence.models import Call, CallEvent, ConversationSession, UsageEvent

pytestmark = pytest.mark.integration
MODEL = "gpt-5.4-nano"  # unique to this test so other tests' rows don't mix in


def test_architecture_labels() -> None:
    assert architecture_of({"voice_provider": "retell", "llm_model": "x"})["architecture"] == "Retell (managed)"
    assert architecture_of({"engine": "realtime", "s2s_model": "m"}) == {
        "key": "realtime|m",
        "architecture": "OpenAI Realtime",
        "model": "m",
    }
    assert architecture_of({"engine": "pipeline", "llm_model": "l"})["architecture"] == "STT → LLM → TTS"


async def _seed(first_audio: list[int], costs: list[tuple[str, str | None]], status: str = "completed") -> None:
    config = resolve_config(SessionConfig(llm_model=MODEL), get_settings())
    async with SessionLocal() as db:
        call = Call(direction="browser", status=status, config_snapshot=config, duration_seconds=60)
        db.add(call)
        await db.flush()
        db.add(
            ConversationSession(
                call_id=call.id, engine="pipeline", status="completed", metrics={"turns": len(first_audio), "avg_stt_ms": 500}
            )
        )
        for ms in first_audio:
            db.add(CallEvent(call_id=call.id, name="audio.playback.started", data={"time_to_first_audio_ms": ms}))
        db.add(CallEvent(call_id=call.id, name="stt.failed", data={}))
        for cost_status, amount in costs:
            db.add(
                UsageEvent(
                    call_id=call.id,
                    provider="openai",
                    model=MODEL,
                    operation_type="llm",
                    usage_source="provider_reported",
                    cost_status=cost_status,
                    cost_usd=Decimal(amount) if amount else None,
                    timestamp=datetime.now(UTC),
                )
            )
        db.add(Call(direction="browser", status="ready", config_snapshot=config))  # never connected: ignored
        await db.commit()


async def test_groups_percentiles_costs_and_flags() -> None:
    await _seed([1000, 2000, 3000], [("calculated", "0.003")])
    await _seed([4000], [("calculated", "0.001"), ("unavailable", None)])
    async with SessionLocal() as db:
        data = await analytics(db, days=1)
    row = next(r for r in data["by_architecture"] if r["model"] == MODEL)
    assert row["sessions"] == 2 and row["turns"] == 4 and row["errors"] == 2
    assert row["first_audio_ms"]["source"] == "measured" and row["first_audio_ms"]["samples"] == 4
    assert row["first_audio_ms"]["p50"] == 2500
    assert Decimal(row["cost_usd"]) == Decimal("0.004")
    assert row["cost_per_turn_usd"] == "0.001000" and row["cost_per_minute_usd"] == "0.002000"
    assert row["cost_complete"] is False  # one usage item had no price
    assert row["stages_ms"]["avg_stt_ms"] == 500
    assert any(p["provider"] == "openai" and p["operation"] == "llm" for p in data["cost_by_provider"])
    assert data["daily"] and data["notes"]
