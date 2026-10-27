"""Retell as a managed voice provider for browser sessions.

Retell runs STT, LLM, TTS, turn-taking and audio transport itself, so the lab
does none of that here: it creates the Retell agent from the lab agent, hands
the browser a web-call token, and afterwards imports what Retell reports
(cost, latency, transcript). Nothing is billed twice: Retell's combined cost is
recorded as one `managed` usage event, with its per-product split in details.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.catalog.pricing import CostResult, CostStatus
from voice_lab.core.config import Settings
from voice_lab.core.database import SessionLocal
from voice_lab.core.errors import LabError
from voice_lab.engines.transcript import TranscriptStore
from voice_lab.integrations.voice.retell.client import RetellClient
from voice_lab.integrations.voice.retell.mapping import RETELL_LLM_MODELS, retell_language
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import Call, ConversationMessage, ConversationSession, ProviderAgentBinding, UsageEvent
from voice_lab.usage.recorder import UsageRecorder

logger = logging.getLogger(__name__)
PROVIDER = "retell"
ENDED = {"ended", "error", "not_connected"}


def retell_spec(config: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Everything Retell sees. Its hash decides whether an existing Retell agent can be reused."""
    return {
        "general_prompt": config["system_prompt"],
        "begin_message": config.get("greeting"),
        "model": RETELL_LLM_MODELS[config["llm_model"]],
        "voice_id": config["retell_voice_id"],
        "language": retell_language(config["language"]),
        "data_storage": settings.retell_data_storage,
        "max_call_ms": settings.max_call_duration_seconds * 1000,
    }


def fingerprint(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def ensure_retell_agent(db: AsyncSession, client: RetellClient, call: Call, settings: Settings) -> ProviderAgentBinding:
    spec = retell_spec(call.config_snapshot, settings)
    key = fingerprint(spec)
    binding = await db.scalar(
        select(ProviderAgentBinding).where(ProviderAgentBinding.provider == PROVIDER, ProviderAgentBinding.fingerprint == key)
    )
    if binding:
        return binding
    llm = await client.create_retell_llm(general_prompt=spec["general_prompt"], begin_message=spec["begin_message"], model=spec["model"])
    agent_info = call.config_snapshot.get("agent") or {}
    agent = await client.create_agent(
        llm_id=llm["llm_id"],
        voice_id=spec["voice_id"],
        name=f"voice-lab · {agent_info.get('name', 'agent')} v{agent_info.get('version', '?')}",
        language=spec["language"],
        data_storage=spec["data_storage"],
        max_call_ms=spec["max_call_ms"],
    )
    binding = ProviderAgentBinding(
        provider=PROVIDER,
        fingerprint=key,
        configuration_id=call.agent_configuration_id,
        external_agent_id=agent["agent_id"],
        external_llm_id=llm["llm_id"],
        details={"retell_model": spec["model"], "voice_id": spec["voice_id"], "language": spec["language"]},
    )
    db.add(binding)
    await db.flush()
    return binding


async def start_web_call(db: AsyncSession, call_id: uuid.UUID, client: RetellClient, settings: Settings) -> dict[str, Any]:
    call = await db.get(Call, call_id)
    if call is None or call.direction != "browser":
        raise LabError("browser session not found", 404)
    if call.config_snapshot.get("voice_provider") != PROVIDER:
        raise LabError("this session doesn't use Retell")
    if call.status != "ready":
        raise LabError(f"session is {call.status}; start a new browser session", 409)
    binding = await ensure_retell_agent(db, client, call, settings)
    web = await client.create_web_call(agent_id=binding.external_agent_id, metadata={"lab_call_id": str(call.id)})
    session = ConversationSession(call_id=call.id, engine="retell", status="active")
    db.add(session)
    call.provider_call_id, call.telephony_provider = web["call_id"], None
    call.status, call.answered_at = "in-progress", datetime.now(UTC)
    await db.commit()
    events = EventRecorder(SessionLocal, call.id, session.id)
    await events.emit(
        "session.started",
        engine="retell",
        provider=PROVIDER,
        transport="retell-web",
        retell_agent_id=binding.external_agent_id,
        model=binding.details.get("retell_model"),
    )
    return {
        "call_id": web["call_id"],
        "access_token": web["access_token"],
        "transport": web.get("transport"),
        "ice_servers": web.get("ice_servers", []),
        "expires_at": web.get("expires_at"),
    }


def _latency_metrics(latency: dict[str, Any] | None) -> dict[str, Any]:
    latency = latency or {}

    def p50(key: str) -> int | None:
        value = (latency.get(key) or {}).get("p50")
        return int(value) if value is not None else None

    return {
        "avg_time_to_first_audio_ms": p50("e2e"),  # Retell's end-to-end turn latency (p50)
        "avg_stt_ms": p50("asr"),
        "avg_llm_ms": p50("llm"),
        "avg_tts_first_byte_ms": p50("tts"),
        "latency_source": "retell (p50)" if latency else None,
    }


async def sync_call(call_id: uuid.UUID, client: RetellClient, client_transcript: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Import Retell's view of a finished web call. Idempotent; safe to call repeatedly."""
    async with SessionLocal() as db:
        call = await db.get(Call, call_id)
        if call is None or not call.provider_call_id or call.config_snapshot.get("voice_provider") != PROVIDER:
            raise LabError("Retell session not found", 404)
        session = await db.scalar(select(ConversationSession).where(ConversationSession.call_id == call.id))
        data = await client.get_call(call.provider_call_id)
        status = data.get("call_status")
        events = EventRecorder(SessionLocal, call.id, session.id if session else None)

        # Transcript: Retell's when it kept one, otherwise what the browser saw live.
        has_messages = session and await db.scalar(
            select(ConversationMessage.id).where(ConversationMessage.session_id == session.id).limit(1)
        )
        utterances = [u for u in (data.get("transcript_object") or []) if u.get("role") in ("agent", "user")]
        source = "retell"
        if not utterances and client_transcript:
            utterances, source = [u for u in client_transcript if u.get("role") in ("agent", "user")], "browser"
        if session and not has_messages and utterances:
            store = TranscriptStore(SessionLocal, session.id)
            for u in utterances:
                if (u.get("content") or "").strip():
                    await store.add(u["role"], u["content"].strip())

        if status in ENDED and call.ended_at is None:
            call.status = "completed" if status == "ended" else "failed"
            call.ended_at = datetime.fromtimestamp(data["end_timestamp"] / 1000, UTC) if data.get("end_timestamp") else datetime.now(UTC)
            if data.get("start_timestamp"):
                call.answered_at = datetime.fromtimestamp(data["start_timestamp"] / 1000, UTC)
            if data.get("duration_ms") is not None:
                call.duration_seconds = round(data["duration_ms"] / 1000)
            if status != "ended":
                call.error = f"retell: {status} ({data.get('disconnection_reason')})"
            if session:
                session.status, session.ended_at = "completed", call.ended_at
                session.metrics = {
                    "engine": "retell",
                    "turns": sum(1 for u in utterances if u["role"] == "user"),
                    **_latency_metrics(data.get("latency")),
                    "disconnection_reason": data.get("disconnection_reason"),
                    "transcript_source": source if utterances else None,
                    "measurement_note": "Latencies reported by Retell (p50 over the call).",
                }
            await db.commit()
            await events.emit(
                "call.completed" if status == "ended" else "call.failed", provider=PROVIDER, reason=data.get("disconnection_reason")
            )

        cost = data.get("call_cost")
        already = await db.scalar(select(UsageEvent.id).where(UsageEvent.call_id == call.id, UsageEvent.operation_type == "managed"))
        # Retell returns a (zero) call_cost before the call happens: only import it once ended.
        final_cost = status in ENDED and cost and cost.get("combined_cost") is not None
        if final_cost and not already:
            usage = UsageRecorder(SessionLocal, events)
            await usage.record(
                provider=PROVIDER,
                model="retell-web-call",
                operation_type="managed",
                cost=CostResult(Decimal(str(cost["combined_cost"])) / 100, CostStatus.PROVIDER_REPORTED, "retell-call-cost"),
                usage_source="provider_reported",
                audio_duration_ms=int(cost.get("total_duration_seconds", 0) * 1000),
                details={
                    "product_costs_cents": cost.get("product_costs"),
                    "llm_token_usage": data.get("llm_token_usage"),
                    "note": "Combined Retell cost (STT+LLM+TTS+platform). Components are not billed separately.",
                },
            )
        return {"call_status": status, "cost_available": bool(final_cost), "transcript_source": source if utterances else None}


async def sync_until_costed(
    call_id: uuid.UUID, client: RetellClient, client_transcript: list[dict[str, str]] | None, attempts: int = 8, delay_s: float = 5
) -> None:
    """Retell finalises cost shortly after a call ends; retry briefly in the background."""
    for attempt in range(attempts):
        try:
            result = await sync_call(call_id, client, client_transcript)
            if result["call_status"] in ENDED and result["cost_available"]:
                return
        except Exception:
            logger.warning("retell sync failed (attempt %s)", attempt + 1, exc_info=True)
        await asyncio.sleep(delay_s)
