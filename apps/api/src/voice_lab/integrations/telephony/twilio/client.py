"""Thin async wrapper around the official Twilio SDK (sync calls run in a thread)."""

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from xml.sax.saxutils import escape, quoteattr

from twilio.request_validator import RequestValidator
from twilio.rest import Client

from voice_lab.core.config import Settings


@dataclass(frozen=True)
class TwilioPrice:
    amount_usd: Decimal | None  # Twilio reports charges as negative numbers
    unit: str | None
    duration_seconds: int | None


def stream_twiml(ws_url: str, parameters: dict[str, str]) -> str:
    params = "".join(f"<Parameter name={quoteattr(k)} value={quoteattr(v)} />" for k, v in parameters.items())
    return f"<Response><Connect><Stream url={quoteattr(ws_url)}>{params}</Stream></Connect></Response>"


def sip_twiml(sip_uri: str, time_limit: int) -> str:
    return f"<Response><Dial timeLimit={quoteattr(str(time_limit))}><Sip>{escape(sip_uri)}</Sip></Dial></Response>"


def reject_twiml() -> str:
    return '<Response><Reject reason="rejected" /></Response>'


class TwilioTelephony:
    STATUS_EVENTS = ["initiated", "ringing", "answered", "completed"]

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        self._validator = RequestValidator(settings.twilio_auth_token)

    async def start_outbound(self, to: str, twiml: str, status_callback: str, time_limit: int) -> str:
        call = await asyncio.to_thread(
            self._client.calls.create,
            to=to,
            from_=self._settings.twilio_phone_number,
            twiml=twiml,
            status_callback=status_callback,
            status_callback_event=self.STATUS_EVENTS,
            status_callback_method="POST",
            time_limit=time_limit,
            record=False,
        )
        return call.sid

    async def hangup(self, call_sid: str) -> None:
        await asyncio.to_thread(self._client.calls(call_sid).update, status="completed")

    async def fetch_price(self, call_sid: str) -> TwilioPrice:
        call = await asyncio.to_thread(self._client.calls(call_sid).fetch)
        amount = abs(Decimal(call.price)) if call.price not in (None, "") else None
        duration = int(call.duration) if call.duration not in (None, "") else None
        return TwilioPrice(amount, call.price_unit, duration)

    async def child_call_sids(self, parent_sid: str) -> list[str]:
        """Legs created by <Dial> (e.g. the SIP leg to Retell). Twilio bills each leg separately."""
        children = await asyncio.to_thread(self._client.calls.list, parent_call_sid=parent_sid, limit=20)
        return [c.sid for c in children]

    async def account_status(self) -> str:
        account = await asyncio.to_thread(self._client.api.v2010.accounts(self._settings.twilio_account_sid).fetch)
        return account.status

    async def find_number(self, phone_number: str) -> dict | None:
        numbers = await asyncio.to_thread(self._client.incoming_phone_numbers.list, phone_number=phone_number, limit=1)
        if not numbers:
            return None
        n = numbers[0]
        return {
            "sid": n.sid,
            "phone_number": n.phone_number,
            "voice_url": n.voice_url,
            "voice_method": n.voice_method,
            "status_callback": n.status_callback,
            "voice_capable": bool((n.capabilities or {}).get("voice")),
        }

    async def configure_number(self, number_sid: str, voice_url: str, status_callback: str) -> None:
        await asyncio.to_thread(
            self._client.incoming_phone_numbers(number_sid).update,
            voice_url=voice_url,
            voice_method="POST",
            status_callback=status_callback,
            status_callback_method="POST",
        )

    def validate(self, url: str, params: dict[str, str], signature: str) -> bool:
        return self._validator.validate(url, params, signature)
