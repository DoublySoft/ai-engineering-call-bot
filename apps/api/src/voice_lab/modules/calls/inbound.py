"""Inbound calls (Phase 5): Twilio calls our voice webhook when someone dials the lab's
number. We pick the agent assigned to that number (or the default agent), create the
call and answer with TwiML: a Media Stream for the lab's engines, or a SIP dial to Retell."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.core.config import Settings
from voice_lab.core.database import SessionLocal
from voice_lab.core.errors import LabError
from voice_lab.core.security import new_stream_token
from voice_lab.integrations.telephony.twilio.client import reject_twiml, sip_twiml, stream_twiml
from voice_lab.integrations.voice.retell.client import RetellClient
from voice_lab.modules.agents.service import resolve_session
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.retell.service import ensure_watcher, register_phone_call
from voice_lab.modules.sessions.config import check_provider_keys, pricing_snapshot
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import Call, VoiceAgent

logger = logging.getLogger(__name__)
APOLOGY = "Lo sentimos, el agente no está disponible en este momento. Inténtelo más tarde."


def apology_twiml() -> str:
    return f'<Response><Say language="es-ES">{APOLOGY}</Say><Hangup /></Response>'


async def agent_for_number(db: AsyncSession, number: str) -> VoiceAgent | None:
    return await db.scalar(select(VoiceAgent).where(VoiceAgent.inbound_number == number, VoiceAgent.archived.is_(False)))


async def _answer(db: AsyncSession, call: Call, settings: Settings, retell: RetellClient | None) -> str:
    if call.config_snapshot["voice_provider"] == "retell":
        if retell is None:
            raise LabError("RETELL_API_KEY is not set", 503)
        sip = await register_phone_call(db, retell, call, settings)
        ensure_watcher(call.id, retell, settings.max_call_duration_seconds)
        return sip_twiml(sip, settings.max_call_duration_seconds)
    call.stream_token = new_stream_token()  # single use; reissued if Twilio retries the webhook
    await db.commit()
    return stream_twiml(f"{settings.public_ws_base}/twilio/media", {"call_id": str(call.id), "token": call.stream_token})


async def handle_inbound(db: AsyncSession, form: dict[str, str], settings: Settings, retell: RetellClient | None) -> str:
    call_sid, caller, called = form.get("CallSid", ""), form.get("From", ""), form.get("To", "")
    existing = await db.scalar(select(Call).where(Call.provider_call_id == call_sid)) if call_sid else None
    if existing is not None:  # Twilio can call the webhook more than once for one call
        try:
            return await _answer(db, existing, settings, retell)
        except LabError:
            return apology_twiml()

    if settings.inbound_allowlist and caller not in settings.inbound_allowlist:
        logger.info("inbound call rejected: caller not in INBOUND_ALLOWLIST")
        return reject_twiml()

    agent = await agent_for_number(db, called)
    try:
        config, agent_cfg = await resolve_session(db, SessionConfig(agent_id=agent.id if agent else None), settings)
        check_provider_keys(config, settings)
    except LabError as exc:
        logger.warning("inbound call can't be answered: %s", exc)
        return apology_twiml()

    now = datetime.now(UTC)
    call = Call(
        direction="inbound",
        status="in-progress",
        from_number=caller,
        to_number=called,
        telephony_provider="twilio",
        provider_call_id=call_sid,
        agent_configuration_id=agent_cfg.id,
        config_snapshot=config,
        pricing_snapshot=pricing_snapshot(config),
        started_at=now,
        answered_at=now,
    )
    db.add(call)
    await db.commit()
    events = EventRecorder(SessionLocal, call.id)
    await events.emit("call.created", provider="twilio", direction="inbound", agent=config["agent"]["name"], engine=config["engine"])
    await events.emit("call.answered", provider="twilio", routed_by="number" if agent else "default_agent")
    try:
        return await _answer(db, call, settings, retell)
    except LabError as exc:
        call.status, call.error = "failed", str(exc)[:300]
        await db.commit()
        await events.emit("call.failed", provider=config["voice_provider"], error=call.error)
        return apology_twiml()
