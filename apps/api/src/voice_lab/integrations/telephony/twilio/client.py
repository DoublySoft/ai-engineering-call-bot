"""Thin async wrapper around the official Twilio SDK (sync calls run in a thread)."""

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from xml.sax.saxutils import quoteattr

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

    def validate(self, url: str, params: dict[str, str], signature: str) -> bool:
        return self._validator.validate(url, params, signature)
