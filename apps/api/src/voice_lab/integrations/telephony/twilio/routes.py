"""Twilio-facing endpoints: status webhooks and the Media Stream WebSocket."""

import base64
import json
import logging

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

from voice_lab.audio.codec import mulaw_to_pcm16
from voice_lab.core.config import Settings, get_settings
from voice_lab.core.database import get_db
from voice_lab.core.websocket import close_quietly
from voice_lab.engines.runner import SessionRunner
from voice_lab.integrations.telephony.twilio.client import TwilioTelephony
from voice_lab.integrations.telephony.twilio.transport import TwilioMediaTransport
from voice_lab.integrations.voice.retell.client import RetellClient
from voice_lab.modules.calls.inbound import apology_twiml, handle_inbound
from voice_lab.modules.calls.service import apply_twilio_status

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


@router.post("/voice")
async def inbound_voice(
    form: dict[str, str] = Depends(verified_twilio_form),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Voice webhook of the lab's Twilio number (set by /api/telephony/configure)."""
    retell = RetellClient(settings.retell_api_key) if settings.retell_api_key else None
    try:
        twiml = await handle_inbound(db, form, settings, retell)
    except Exception:  # never leave a caller in silence
        logger.exception("inbound webhook failed")
        twiml = apology_twiml()
    return Response(content=twiml, media_type="application/xml")


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
    runner = SessionRunner()
    end_reason = "stream_stopped"
    try:
        while True:
            message = json.loads(await ws.receive_text())
            kind = message.get("event")
            if kind == "start":
                start = message["start"]
                params = start.get("customParameters", {})
                telephony = TwilioTelephony(settings) if settings.twilio_configured else None
                transport = TwilioMediaTransport(ws, start["streamSid"], start["callSid"], telephony)
                if not await runner.open(params.get("call_id", ""), params.get("token", ""), transport):
                    await ws.close(code=1008)
                    return
            elif kind == "media":
                if message["media"].get("track", "inbound") == "inbound":
                    await runner.feed(mulaw_to_pcm16(base64.b64decode(message["media"]["payload"])))
            elif kind == "mark":
                await runner.playback_done(message["mark"]["name"])
            elif kind == "stop":
                break
    except WebSocketDisconnect:
        end_reason = "websocket_disconnected"
    except Exception:
        end_reason = "error"
        logger.exception("media stream crashed")
    finally:
        await runner.close(end_reason)
        await close_quietly(ws)
