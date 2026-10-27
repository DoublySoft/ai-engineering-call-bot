import asyncio
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from voice_lab.catalog.registry import all_models
from voice_lab.core.config import Settings, get_settings
from voice_lab.core.database import get_db
from voice_lab.core.errors import LabError
from voice_lab.core.security import events_token, require_admin, verify_events_token
from voice_lab.engines.realtime import REALTIME_VOICES
from voice_lab.integrations.registry import IMPLEMENTED
from voice_lab.integrations.telephony.twilio.client import TwilioTelephony
from voice_lab.integrations.voice.retell.client import RetellClient
from voice_lab.integrations.voice.retell.mapping import RETELL_LLM_MODELS
from voice_lab.modules.calls.schemas import CallDetail, CallSummary, OutboundCallRequest
from voice_lab.modules.calls.service import start_outbound_call
from voice_lab.observability.hub import hub
from voice_lab.persistence.models import (
    Call,
    CallEvent,
    ConversationSession,
    UsageEvent,
)

router = APIRouter(prefix="/api", tags=["lab"])
admin = [Depends(require_admin)]


def _row(obj: Any, *exclude: str) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns if c.key not in exclude}


@router.get("/catalog", dependencies=admin)
async def catalog(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "models": [
            {**m.model_dump(mode="json"), "key": m.key, "implemented": m.provider.value in IMPLEMENTED.get(m.kind.value, set())}
            for m in all_models()
        ],
        "implemented": {k: sorted(v) for k, v in IMPLEMENTED.items()},
        "defaults": {
            "stt_provider": settings.stt_provider,
            "stt_model": settings.stt_model,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "tts_provider": settings.tts_provider,
            "tts_model": settings.tts_model,
            "tts_voice": settings.tts_voice,
            "language": settings.agent_language,
            "s2s_model": settings.s2s_model,
            "retell_voice_id": settings.retell_voice_id,
        },
        "outbound_allowlist": settings.outbound_allowlist,
        "voices": ["alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"],
        "browser_audio": {"input_sample_rate": 16000, "output_sample_rate": 24000},
        "realtime_voices": sorted(REALTIME_VOICES),
        "retell": {
            "llm_models": sorted(RETELL_LLM_MODELS),
            "default_voice_id": settings.retell_voice_id,
            "data_storage": settings.retell_data_storage,
        },
        "readiness": {
            "twilio": settings.twilio_configured,
            "openai": bool(settings.openai_api_key),
            "anthropic": bool(settings.anthropic_api_key),
            "retell": bool(settings.retell_api_key),
            "public_base_url": settings.public_base_url.startswith("https://"),
        },
    }


@router.post("/calls", dependencies=admin, status_code=201)
async def create_call(
    request: OutboundCallRequest, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    try:
        telephony = TwilioTelephony(settings) if settings.twilio_configured else None
        retell = RetellClient(settings.retell_api_key) if settings.retell_api_key else None
        call = await start_outbound_call(db, request, settings, telephony, retell)  # type: ignore[arg-type]
    except LabError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"id": str(call.id), "status": call.status}


@router.post("/calls/{call_id}/hangup", dependencies=admin)
async def hangup(call_id: uuid.UUID, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict[str, str]:
    call = await db.get(Call, call_id)
    if call is None:
        raise HTTPException(404)
    if call.provider_call_id and settings.twilio_configured:
        await TwilioTelephony(settings).hangup(call.provider_call_id)
    return {"status": "hangup_requested"}


@router.get("/calls", dependencies=admin)
async def list_calls(limit: int = Query(50, le=200), db: AsyncSession = Depends(get_db)) -> list[CallSummary]:
    calls = (await db.scalars(select(Call).order_by(Call.created_at.desc()).limit(limit))).all()
    totals = await _cost_totals(db, [c.id for c in calls])
    return [
        CallSummary(
            id=c.id,
            direction=c.direction,
            status=c.status,
            to_number=c.to_number,
            from_number=c.from_number,
            engine=c.config_snapshot.get("engine"),
            llm_model=c.config_snapshot.get("llm_model"),
            created_at=c.created_at,
            duration_seconds=c.duration_seconds,
            total_cost_usd=totals.get(c.id),
            error=c.error,
        )
        for c in calls
    ]


async def _cost_totals(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    if not ids:
        return {}
    rows = (await db.execute(select(UsageEvent.call_id, UsageEvent.cost_usd).where(UsageEvent.call_id.in_(ids)))).all()
    totals: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for call_id, cost in rows:
        totals[call_id] += cost or 0
    return totals


@router.get("/calls/{call_id}", dependencies=admin)
async def call_detail(call_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> CallDetail:
    call = await db.scalar(
        select(Call).where(Call.id == call_id).options(selectinload(Call.sessions).selectinload(ConversationSession.messages))
    )
    if call is None:
        raise HTTPException(404)
    events = (await db.scalars(select(CallEvent).where(CallEvent.call_id == call_id).order_by(CallEvent.timestamp))).all()
    usage = (await db.scalars(select(UsageEvent).where(UsageEvent.call_id == call_id).order_by(UsageEvent.timestamp))).all()

    by_op: dict[str, dict[str, Any]] = {}
    for u in usage:
        agg = by_op.setdefault(
            u.operation_type, {"cost_usd": Decimal(0), "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "statuses": set()}
        )
        agg["cost_usd"] += u.cost_usd or 0
        for f in ("input_tokens", "output_tokens", "cached_tokens"):
            agg[f] += getattr(u, f) or 0
        agg["statuses"].add(u.cost_status)
    for agg in by_op.values():
        agg["statuses"] = sorted(agg["statuses"])

    return CallDetail(
        call=_row(call, "stream_token"),
        sessions=[_row(s) for s in call.sessions],
        messages=[{**_row(m), "session_id": str(s.id)} for s in call.sessions for m in s.messages],
        events=[_row(e) for e in events],
        usage=[_row(u) for u in usage],
        cost_by_operation=by_op,
        events_token=events_token(str(call_id)),
    )


@router.websocket("/calls/{call_id}/live")
async def live(ws: WebSocket, call_id: uuid.UUID, token: str = "") -> None:
    if not verify_events_token(str(call_id), token):
        await ws.close(code=1008)
        return
    await ws.accept()
    queue = hub.subscribe(str(call_id))
    try:
        while True:
            receive = asyncio.ensure_future(ws.receive_text())
            get = asyncio.ensure_future(queue.get())
            done, pending = await asyncio.wait({receive, get}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if receive in done:
                receive.result()  # raises on disconnect; client messages are ignored
                continue
            await ws.send_json(get.result())
    except Exception:
        pass
    finally:
        hub.unsubscribe(str(call_id), queue)
