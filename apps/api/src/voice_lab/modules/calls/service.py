import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.catalog.pricing import CostResult, CostStatus, calculate_cost
from voice_lab.catalog.registry import get_model
from voice_lab.catalog.schemas import BillingDimension, ModelKind
from voice_lab.core.config import Settings
from voice_lab.core.database import SessionLocal
from voice_lab.core.security import new_stream_token
from voice_lab.integrations.registry import IMPLEMENTED
from voice_lab.integrations.telephony.twilio.client import TwilioTelephony, stream_twiml
from voice_lab.modules.calls.schemas import OutboundCallRequest, SessionConfig
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import Call, VoiceAgent, VoiceAgentConfiguration
from voice_lab.usage.recorder import UsageRecorder

logger = logging.getLogger(__name__)


class CallError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def resolve_config(cfg: SessionConfig, settings: Settings) -> dict[str, Any]:
    """Fill defaults and reject combinations the lab can't run yet."""
    resolved = cfg.model_dump()
    for part in ("stt", "llm", "tts"):
        resolved[f"{part}_provider"] = resolved[f"{part}_provider"] or getattr(settings, f"{part}_provider")
        resolved[f"{part}_model"] = resolved[f"{part}_model"] or getattr(settings, f"{part}_model")
    resolved["tts_voice"] = resolved["tts_voice"] or settings.tts_voice
    resolved["language"] = resolved["language"] or settings.agent_language

    if resolved["voice_provider"] not in IMPLEMENTED["voice_provider"]:
        raise CallError(f"voice provider '{resolved['voice_provider']}' is not implemented yet (planned: Phase 7)")
    if resolved["engine"] not in IMPLEMENTED["engine"]:
        raise CallError(f"engine '{resolved['engine']}' is not implemented yet (planned: Phase 6)")
    for part, kind in (("stt", ModelKind.STT), ("llm", ModelKind.LLM), ("tts", ModelKind.TTS)):
        provider, model = resolved[f"{part}_provider"], resolved[f"{part}_model"]
        if provider not in IMPLEMENTED[part]:
            raise CallError(f"{part.upper()} provider '{provider}' is not implemented yet")
        definition = get_model(provider, model)
        if definition is None or definition.kind != kind:
            raise CallError(f"'{provider}:{model}' is not a known {kind.value} model in the catalog")
    return resolved


def pricing_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for part in ("stt", "llm", "tts"):
        definition = get_model(config[f"{part}_provider"], config[f"{part}_model"])
        pricing = definition.current_pricing() if definition else None
        snapshot[part] = pricing.model_dump(mode="json") if pricing else None
    return snapshot


async def ensure_agent_configuration(db: AsyncSession, config: dict[str, Any]) -> VoiceAgentConfiguration:
    agent = await db.scalar(select(VoiceAgent).where(VoiceAgent.is_default.is_(True)))
    if agent is None:
        agent = VoiceAgent(name="default", is_default=True)
        db.add(agent)
        await db.flush()
    settings_json = {k: v for k, v in config.items() if k not in ("voice_provider", "engine")}
    existing = (
        await db.scalars(
            select(VoiceAgentConfiguration)
            .where(VoiceAgentConfiguration.agent_id == agent.id)
            .order_by(VoiceAgentConfiguration.version.desc())
        )
    ).all()
    for cfg in existing:
        if cfg.settings == settings_json and cfg.engine == config["engine"] and cfg.voice_provider == config["voice_provider"]:
            return cfg
    cfg = VoiceAgentConfiguration(
        agent_id=agent.id,
        version=(existing[0].version + 1) if existing else 1,
        voice_provider=config["voice_provider"],
        engine=config["engine"],
        settings=settings_json,
    )
    db.add(cfg)
    await db.flush()
    return cfg


def check_provider_keys(config: dict[str, Any], settings: Settings) -> None:
    """Fail before dialing/connecting if a selected provider has no API key."""
    keys = {"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key}
    for part in ("stt", "llm", "tts"):
        provider = config[f"{part}_provider"]
        if provider in keys and not keys[provider]:
            raise CallError(f"{provider.upper()}_API_KEY is not set (needed for {part.upper()})", 503)


def check_outbound_prerequisites(settings: Settings, to: str) -> None:
    if to not in settings.outbound_allowlist:
        raise CallError("number is not in OUTBOUND_ALLOWLIST (test numbers only)", 403)
    if not settings.twilio_configured:
        raise CallError("Twilio is not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER)", 503)
    if not settings.public_base_url.startswith("https://"):
        raise CallError("PUBLIC_BASE_URL must be an https URL Twilio can reach (e.g. an ngrok tunnel)", 503)


async def start_outbound_call(db: AsyncSession, request: OutboundCallRequest, settings: Settings, telephony: TwilioTelephony) -> Call:
    check_outbound_prerequisites(settings, request.to)
    config = resolve_config(request.config, settings)
    check_provider_keys(config, settings)
    agent_cfg = await ensure_agent_configuration(db, config)

    call = Call(
        direction="outbound",
        status="queued",
        to_number=request.to,
        from_number=settings.twilio_phone_number,
        telephony_provider="twilio",
        agent_configuration_id=agent_cfg.id,
        config_snapshot=config,
        pricing_snapshot=pricing_snapshot(config),
        stream_token=new_stream_token(),
        started_at=datetime.now(UTC),
    )
    db.add(call)
    await db.commit()
    events = EventRecorder(SessionLocal, call.id)
    await events.emit("call.created", provider="twilio", direction="outbound", engine=config["engine"], llm_model=config["llm_model"])

    twiml = stream_twiml(
        f"{settings.public_ws_base}/twilio/media",
        {"call_id": str(call.id), "token": call.stream_token or ""},
    )
    try:
        sid = await telephony.start_outbound(
            to=request.to,
            twiml=twiml,
            status_callback=f"{settings.public_base_url.rstrip('/')}/twilio/status",
            time_limit=settings.max_call_duration_seconds,
        )
    except Exception as exc:
        call.status, call.error = "failed", f"twilio: {type(exc).__name__}: {str(exc)[:300]}"
        await db.commit()
        await events.emit("call.failed", provider="twilio", error=call.error)
        raise CallError("Twilio rejected the call; see call detail for the error", 502) from exc

    call.provider_call_id, call.status = sid, "initiated"
    await db.commit()
    return call


TWILIO_STATUS_EVENTS = {
    "ringing": "call.ringing",
    "in-progress": "call.answered",
    "completed": "call.completed",
    "busy": "call.failed",
    "failed": "call.failed",
    "no-answer": "call.failed",
    "canceled": "call.failed",
}


async def apply_twilio_status(
    db: AsyncSession, call_sid: str, status: str, duration: str | None, telephony: TwilioTelephony | None
) -> Call | None:
    call = await db.scalar(select(Call).where(Call.provider_call_id == call_sid))
    if call is None:
        return None
    now = datetime.now(UTC)
    call.status = status
    if status == "in-progress" and call.answered_at is None:
        call.answered_at = now
    if status in ("completed", "busy", "failed", "no-answer", "canceled"):
        call.ended_at = call.ended_at or now
        if duration and duration.isdigit():
            call.duration_seconds = int(duration)
    await db.commit()
    events = EventRecorder(SessionLocal, call.id)
    name = TWILIO_STATUS_EVENTS.get(status)
    if name:
        await events.emit(name, provider="twilio", twilio_status=status, duration_s=call.duration_seconds)
    if status == "completed" and telephony is not None:
        asyncio.create_task(record_telephony_cost(call.id, call_sid, call.duration_seconds, telephony))
    return call


async def record_telephony_cost(
    call_id: uuid.UUID, call_sid: str, duration_s: int | None, telephony: TwilioTelephony, attempts: int = 6, delay_s: float = 20
) -> None:
    """Twilio fills `price` asynchronously after the call ends; poll briefly for it."""
    events = EventRecorder(SessionLocal, call_id)
    usage = UsageRecorder(SessionLocal, events)
    for attempt in range(attempts):
        try:
            price = await telephony.fetch_price(call_sid)
        except Exception as exc:
            logger.warning("twilio price fetch failed: %s", type(exc).__name__)
            price = None
        if price and price.amount_usd is not None and price.unit == "USD":
            await usage.record(
                provider="twilio",
                model="programmable-voice",
                operation_type="telephony",
                cost=CostResult(price.amount_usd, CostStatus.PROVIDER_REPORTED, "twilio-call-resource"),
                usage_source="provider_reported",
                audio_duration_ms=(price.duration_seconds or duration_s or 0) * 1000,
                details={"price_unit": price.unit},
            )
            return
        if attempt < attempts - 1:
            await asyncio.sleep(delay_s)
    await usage.record(
        provider="twilio",
        model="programmable-voice",
        operation_type="telephony",
        cost=calculate_cost(None, {BillingDimension.CALL_SECONDS: duration_s or 0}),
        usage_source="provider_reported",
        audio_duration_ms=(duration_s or 0) * 1000,
        details={"note": "Twilio had not published the price yet; check the Twilio console."},
    )
