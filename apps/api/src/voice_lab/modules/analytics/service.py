"""Comparisons across architectures, providers and models.

Lab-scale on purpose: rows for the window are loaded and aggregated in Python.
Every figure says where it comes from: measured by the lab, reported by a
provider, or not available. Nothing is averaged across those kinds silently.
"""

import statistics
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.persistence.models import Call, CallEvent, ConversationSession, UsageEvent


def architecture_of(config: dict[str, Any]) -> dict[str, str]:
    provider = config.get("voice_provider", "custom")
    engine = config.get("engine", "pipeline")
    if provider == "retell":
        key, label, model = f"retell|{config.get('llm_model')}", "Retell (managed)", config.get("llm_model", "?")
    elif engine == "realtime":
        key, label, model = f"realtime|{config.get('s2s_model')}", "OpenAI Realtime", config.get("s2s_model", "?")
    else:
        key, label, model = f"pipeline|{config.get('llm_model')}", "STT → LLM → TTS", config.get("llm_model", "?")
    return {"key": key, "architecture": label, "model": model}


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1])


async def analytics(db: AsyncSession, days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    calls = (await db.scalars(select(Call).where(Call.created_at >= since))).all()
    call_ids = [c.id for c in calls]
    sessions = (await db.scalars(select(ConversationSession).where(ConversationSession.call_id.in_(call_ids)))).all() if call_ids else []
    usage = (await db.scalars(select(UsageEvent).where(UsageEvent.call_id.in_(call_ids)))).all() if call_ids else []
    first_audio = (
        (
            await db.execute(
                select(CallEvent.call_id, CallEvent.data).where(CallEvent.call_id.in_(call_ids), CallEvent.name == "audio.playback.started")
            )
        ).all()
        if call_ids
        else []
    )
    failures = (
        (
            await db.execute(
                select(CallEvent.call_id, CallEvent.name).where(CallEvent.call_id.in_(call_ids), CallEvent.name.like("%.failed"))
            )
        ).all()
        if call_ids
        else []
    )

    arch_by_call = {c.id: architecture_of(c.config_snapshot) for c in calls}
    sessions_by_call: dict[uuid.UUID, list[ConversationSession]] = defaultdict(list)
    for s in sessions:
        sessions_by_call[s.call_id].append(s)

    groups: dict[str, dict[str, Any]] = {}

    def group(call_id: uuid.UUID) -> dict[str, Any]:
        arch = arch_by_call[call_id]
        return groups.setdefault(
            arch["key"],
            {
                **arch,
                "sessions": 0,
                "completed": 0,
                "turns": 0,
                "duration_seconds": 0,
                "cost_usd": Decimal(0),
                "cost_statuses": set(),
                "errors": 0,
                "first_audio_ms": [],  # per turn, measured by the lab
                "reported_first_audio_ms": [],  # per session p50, reported by a managed provider
                "stage_ms": defaultdict(list),
            },
        )

    for c in calls:
        g = group(c.id)
        if c.status == "ready":
            continue  # created but never connected
        g["sessions"] += 1
        g["completed"] += c.status == "completed"
        g["duration_seconds"] += c.duration_seconds or 0
        for s in sessions_by_call.get(c.id, []):
            m = s.metrics or {}
            g["turns"] += m.get("turns") or 0
            for key in ("avg_stt_ms", "avg_llm_ttft_ms", "avg_tts_first_byte_ms", "avg_response_ms"):
                if m.get(key) is not None:
                    g["stage_ms"][key].append(m[key])
            if m.get("engine") == "retell" and m.get("avg_time_to_first_audio_ms") is not None:
                g["reported_first_audio_ms"].append(m["avg_time_to_first_audio_ms"])
    for call_id, data in first_audio:
        if call_id in arch_by_call and (data or {}).get("time_to_first_audio_ms") is not None:
            group(call_id)["first_audio_ms"].append(data["time_to_first_audio_ms"])
    for call_id, _ in failures:
        if call_id in arch_by_call:
            group(call_id)["errors"] += 1

    by_provider: dict[tuple[str, str], dict[str, Any]] = {}
    daily: dict[str, dict[str, Any]] = defaultdict(lambda: {"cost_usd": Decimal(0), "sessions": 0})
    for u in usage:
        g = group(u.call_id)
        g["cost_usd"] += u.cost_usd or 0
        g["cost_statuses"].add(u.cost_status)
        p = by_provider.setdefault(
            (u.provider, u.operation_type),
            {"provider": u.provider, "operation": u.operation_type, "cost_usd": Decimal(0), "events": 0, "statuses": set()},
        )
        p["cost_usd"] += u.cost_usd or 0
        p["events"] += 1
        p["statuses"].add(u.cost_status)
        daily[u.timestamp.date().isoformat()]["cost_usd"] += u.cost_usd or 0
    for c in calls:
        if c.status != "ready":
            daily[c.created_at.date().isoformat()]["sessions"] += 1

    rows = []
    for g in groups.values():
        if not g["sessions"]:
            continue
        measured = sorted(g["first_audio_ms"])
        turns = g["turns"] or None
        rows.append(
            {
                "architecture": g["architecture"],
                "model": g["model"],
                "sessions": g["sessions"],
                "completed": g["completed"],
                "turns": g["turns"],
                "duration_seconds": g["duration_seconds"],
                "errors": g["errors"],
                "first_audio_ms": {
                    "source": "measured" if measured else ("provider_reported" if g["reported_first_audio_ms"] else None),
                    "p50": _percentile(measured, 50) if measured else _percentile(sorted(g["reported_first_audio_ms"]), 50),
                    "p90": _percentile(measured, 90) if measured else None,
                    "samples": len(measured) or len(g["reported_first_audio_ms"]),
                },
                "stages_ms": {k: int(statistics.mean(v)) for k, v in g["stage_ms"].items() if v},
                "cost_usd": str(g["cost_usd"]),
                "cost_per_turn_usd": str((g["cost_usd"] / turns).quantize(Decimal("0.000001"))) if turns else None,
                "cost_per_minute_usd": str((g["cost_usd"] / g["duration_seconds"] * 60).quantize(Decimal("0.000001")))
                if g["duration_seconds"]
                else None,
                "cost_complete": g["cost_statuses"] <= {"calculated", "provider_reported"},
            }
        )
    rows.sort(key=lambda r: (r["first_audio_ms"]["p50"] is None, r["first_audio_ms"]["p50"] or 0))
    return {
        "window_days": days,
        "by_architecture": rows,
        "cost_by_provider": [
            {**p, "cost_usd": str(p["cost_usd"]), "statuses": sorted(p["statuses"])}
            for p in sorted(by_provider.values(), key=lambda p: -p["cost_usd"])
        ],
        "daily": [{"date": d, "cost_usd": str(v["cost_usd"]), "sessions": v["sessions"]} for d, v in sorted(daily.items())],
        "notes": [
            "first_audio_ms = end of the caller's turn → first agent audio sent, measured server-side; network/playout not included.",
            "Pipeline turns end after the lab's VAD silence (700 ms); Realtime after the provider's server VAD (500 ms).",
            "Retell figures are Retell's own e2e p50 per call (provider_reported), not lab measurements.",
        ],
    }
