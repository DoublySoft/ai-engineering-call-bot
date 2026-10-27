"""Telephony readiness: everything the lab can check before a real phone call, so
that after buying a Twilio number the only steps are .env + one "Configure" click."""

from dataclasses import asdict, dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.core.config import Settings
from voice_lab.core.errors import LabError
from voice_lab.integrations.telephony.twilio.client import TwilioTelephony
from voice_lab.modules.agents.service import resolve_session
from voice_lab.modules.calls.inbound import agent_for_number
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.sessions.config import check_provider_keys
from voice_lab.persistence.models import VoiceAgent


@dataclass
class Check:
    key: str
    label: str
    ok: bool | None  # None = not checked (a previous step failed) or optional
    detail: str = ""
    needed_for: str = "both"  # outbound | inbound | both | optional


def webhook_urls(settings: Settings) -> dict[str, str]:
    base = settings.public_base_url.rstrip("/")
    return {
        "voice_url": f"{base}/twilio/voice",
        "status_callback": f"{base}/twilio/status",
        "media_stream": f"{settings.public_ws_base}/twilio/media",
    }


async def _public_url_reaches_us(url: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=False) as client:
            r = await client.get(f"{url.rstrip('/')}/health")
    except httpx.HTTPError as exc:
        return False, f"not reachable ({type(exc).__name__}); is the tunnel running?"
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json") and r.json().get("status"):
        return True, "reaches this API"
    return False, f"answered {r.status_code}, but not as this API (wrong tunnel target or port?)"


async def _agent_keys_check(db: AsyncSession, settings: Settings, agent_id: Any, label: str, scope: str) -> Check:
    try:
        config, _ = await resolve_session(db, SessionConfig(agent_id=agent_id), settings)
    except LabError as exc:
        return Check(f"agent_{scope}", f"{label} can run", False, str(exc), scope)
    arch = "Retell" if config["voice_provider"] == "retell" else ("OpenAI Realtime" if config["engine"] == "realtime" else "pipeline")
    name = f"{config['agent']['name']} ({arch})"
    try:
        check_provider_keys(config, settings)
    except LabError as exc:
        return Check(f"agent_{scope}", f"{label} has its provider keys", False, f"{name}: {exc}", scope)
    return Check(f"agent_{scope}", f"{label} has its provider keys", True, name, scope)


async def telephony_status(db: AsyncSession, settings: Settings, telephony: TwilioTelephony | None = None) -> dict[str, Any]:
    urls = webhook_urls(settings)
    checks: list[Check] = []
    env_ok = settings.twilio_configured
    checks.append(Check("twilio_env", "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER set", env_ok))
    placeholder = "your-tunnel.example.com" in settings.public_base_url  # the .env.example value
    https = settings.public_base_url.startswith("https://") and not placeholder
    checks.append(
        Check(
            "public_url",
            "PUBLIC_BASE_URL is an https URL",
            https,
            "still the .env.example placeholder" if placeholder else (settings.public_base_url or "not set"),
        )
    )

    number: dict | None = None
    if env_ok:
        telephony = telephony or TwilioTelephony(settings)
        try:
            status = await telephony.account_status()
            checks.append(Check("credentials", "Twilio credentials are valid", status == "active", f"account {status}"))
            number = await telephony.find_number(settings.twilio_phone_number)
            checks.append(
                Check(
                    "number",
                    f"{settings.twilio_phone_number} belongs to this Twilio account and supports voice",
                    bool(number and number["voice_capable"]),
                    "found" if number else "not found in this account (buy it or fix TWILIO_PHONE_NUMBER)",
                )
            )
        except Exception as exc:  # bad credentials, network
            checks.append(Check("credentials", "Twilio credentials are valid", False, type(exc).__name__))
    else:
        checks.append(Check("credentials", "Twilio credentials are valid", None, "set the Twilio variables first"))

    if https:
        reachable, detail = await _public_url_reaches_us(settings.public_base_url)
        checks.append(Check("tunnel", "PUBLIC_BASE_URL reaches this API (Twilio can call the webhooks)", reachable, detail))
    else:
        checks.append(Check("tunnel", "PUBLIC_BASE_URL reaches this API (Twilio can call the webhooks)", None, "needs an https URL"))

    if number:
        checks.append(
            Check(
                "voice_webhook",
                "Number's incoming-call webhook points to this lab",
                number["voice_url"] == urls["voice_url"] and (number["voice_method"] or "POST").upper() == "POST",
                number["voice_url"] or "not set",
                "inbound",
            )
        )
        checks.append(
            Check(
                "status_webhook",
                "Number's call-status webhook points to this lab",
                number["status_callback"] == urls["status_callback"],
                number["status_callback"] or "not set",
                "inbound",
            )
        )
    else:
        checks.append(Check("voice_webhook", "Number's incoming-call webhook points to this lab", None, "", "inbound"))
        checks.append(Check("status_webhook", "Number's call-status webhook points to this lab", None, "", "inbound"))

    checks.append(
        Check(
            "outbound_allowlist",
            "OUTBOUND_ALLOWLIST has at least one test number",
            bool(settings.outbound_allowlist),
            ", ".join(settings.outbound_allowlist) or "empty",
            "outbound",
        )
    )
    # AI keys depend on who answers: check the agent's real architecture, exactly as a call would.
    inbound_agent = await agent_for_number(db, settings.twilio_phone_number) if settings.twilio_phone_number else None
    checks.append(
        await _agent_keys_check(db, settings, inbound_agent.id if inbound_agent else None, "Agent answering the lab's number", "inbound")
    )
    checks.append(await _agent_keys_check(db, settings, None, "Default agent (outbound calls; can be overridden per call)", "outbound"))

    def ready(scope: str) -> bool:
        return all(c.ok for c in checks if c.needed_for in ("both", scope))

    routed = (await db.scalars(select(VoiceAgent).where(VoiceAgent.inbound_number.is_not(None), VoiceAgent.archived.is_(False)))).all()
    default = await db.scalar(select(VoiceAgent).where(VoiceAgent.is_default.is_(True)))
    return {
        "ready": {"outbound": ready("outbound"), "inbound": ready("inbound")},
        "checks": [asdict(c) for c in checks],
        "webhooks": urls,
        "phone_number": settings.twilio_phone_number or None,
        "inbound_routing": {
            "by_number": [{"number": a.inbound_number, "agent_id": str(a.id), "agent": a.name} for a in routed],
            "default_agent": default.name if default else None,
            "allowlist": settings.inbound_allowlist,
        },
    }


async def configure_number(settings: Settings, telephony: TwilioTelephony | None = None) -> dict[str, str]:
    """Point the Twilio number's voice and status webhooks at this lab."""
    if not settings.twilio_configured:
        raise LabError("Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER first", 503)
    if not settings.public_base_url.startswith("https://"):
        raise LabError("PUBLIC_BASE_URL must be an https URL that reaches this API", 503)
    telephony = telephony or TwilioTelephony(settings)
    number = await telephony.find_number(settings.twilio_phone_number)
    if number is None:
        raise LabError(f"{settings.twilio_phone_number} is not in this Twilio account", 404)
    urls = webhook_urls(settings)
    await telephony.configure_number(number["sid"], urls["voice_url"], urls["status_callback"])
    return {"phone_number": number["phone_number"], **urls}
