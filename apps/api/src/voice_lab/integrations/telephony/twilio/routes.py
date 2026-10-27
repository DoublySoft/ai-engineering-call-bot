"""Twilio-facing endpoints: status webhooks and the Media Stream WebSocket."""

import asyncio
import base64
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from voice_lab.audio.codec import mulaw_to_pcm16
from voice_lab.core.config import Settings, get_settings
from voice_lab.core.database import SessionLocal, get_db
from voice_lab.engines.base import ConversationEngine, SessionContext
from voice_lab.engines.factory import create_engine
from voice_lab.integrations.telephony.twilio.client import TwilioTelephony
from voice_lab.integrations.telephony.twilio.transport import TwilioMediaTransport
from voice_lab.modules.calls.service import apply_twilio_status
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import Call, ConversationSession
from voice_lab.usage.recorder import UsageRecorder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])


def get_telephony(settings: Settings = Depends(get_settings)) -> TwilioTelephony | None:
    return TwilioTelephony(settings) if settings.twilio_configured else None


async def verified_twilio_form(request: Request, settings: Settings = Depends(get_settings)) -> dict[str, str]:
    """Validates X-Twilio-Signature against the public URL Twilio called."""
    if not settings.twilio_configured:
        raise HTTPException(503, "Twilio is not configured")
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    signature = request.headers.get("X-Twilio-Signature", "")
    if not TwilioTelephony(settings).validate(url, form, signature):
        raise HTTPException(403, "invalid Twilio signature")
    return form


@router.post("/status")
async def status_callback(
    form: dict[str, str] = Depends(verified_twilio_form),
    db: AsyncSession = Depends(get_db),
    telephony: TwilioTelephony | None = Depends(get_telephony),
) -> Response:
    await apply_twilio_status(db, form.get("CallSid", ""), form.get("CallStatus", ""), form.get("CallDuration"), telephony)
    return Response(status_code=204)


@router.websocket("/media")
async def media_stream(ws: WebSocket) -> None:
    """Bidirectional Media Stream. Authenticated by the per-call token we put in
    the TwiML <Parameter>, which only Twilio (and our DB) know."""
    await ws.accept()
    settings = get_settings()
    engine: ConversationEngine | None = None
    session_row_id: uuid.UUID | None = None
    events: EventRecorder | None = None
    watchdog: asyncio.Task[None] | None = None
    transport: TwilioMediaTransport | None = None
    end_reason = "stream_stopped"
    try:
        while True:
            message = json.loads(await ws.receive_text())
            kind = message.get("event")
            if kind == "start":
                start = message["start"]
                params = start.get("customParameters", {})
                async with SessionLocal() as db:
                    call = await db.get(Call, uuid.UUID(params.get("call_id", "00000000-0000-0000-0000-000000000000")))
                    if call is None or not call.stream_token or not hmac.compare_digest(call.stream_token, params.get("token", "")):
                        logger.warning("media stream rejected: bad call id or token")
                        await ws.close(code=1008)
                        return
                    call.stream_token = None  # single use
                    session = ConversationSession(call_id=call.id, engine=call.config_snapshot["engine"], status="active")
                    db.add(session)
                    await db.commit()
                    session_row_id, config, call_id = session.id, call.config_snapshot, call.id
                telephony = TwilioTelephony(settings) if settings.twilio_configured else None
                transport = TwilioMediaTransport(ws, start["streamSid"], start["callSid"], telephony)
                events = EventRecorder(SessionLocal, call_id, session_row_id)
                ctx = SessionContext(
                    call_id=call_id,
                    session_id=session_row_id,
                    config=config,
                    transport=transport,
                    events=events,
                    usage=UsageRecorder(SessionLocal, events),
                    sessionmaker=SessionLocal,
                    max_cost_usd=Decimal(settings.max_call_cost_usd),
                )
                try:
                    engine = create_engine(ctx)
                except Exception as exc:
                    await events.emit("session.failed", error=str(exc)[:300])
                    await transport.hangup("engine_unavailable")
                    return
                await engine.start()
                watchdog = asyncio.create_task(_duration_watchdog(transport, events, settings.max_call_duration_seconds))
            elif kind == "media" and engine is not None:
                if message["media"].get("track", "inbound") == "inbound":
                    await engine.handle_audio(mulaw_to_pcm16(base64.b64decode(message["media"]["payload"])))
            elif kind == "mark" and engine is not None:
                await engine.on_playback_done(message["mark"]["name"])
            elif kind == "stop":
                break
    except WebSocketDisconnect:
        end_reason = "websocket_disconnected"
    except Exception:
        end_reason = "error"
        logger.exception("media stream crashed")
    finally:
        if watchdog:
            watchdog.cancel()
        if transport and transport.hung_up_reason:
            end_reason = transport.hung_up_reason
        # Shielded so a server shutdown mid-call still persists the session state.
        await asyncio.shield(_finalize(engine, session_row_id, end_reason))
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close()


async def _finalize(engine: ConversationEngine | None, session_row_id: uuid.UUID | None, end_reason: str) -> None:
    metrics = await engine.end(end_reason) if engine else {}
    if session_row_id:
        async with SessionLocal() as db:
            session = await db.get(ConversationSession, session_row_id)
            if session:
                session.status, session.ended_at, session.metrics = "completed", datetime.now(UTC), metrics
                await db.commit()


async def _duration_watchdog(transport: TwilioMediaTransport, events: EventRecorder, limit_s: int) -> None:
    """Backstop for Twilio's own time_limit."""
    await asyncio.sleep(limit_s + 5)
    await events.emit("call.duration_limit_reached", limit_s=limit_s)
    await transport.hangup("duration_limit")
