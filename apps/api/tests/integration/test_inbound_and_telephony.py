import asyncio
import uuid
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from voice_lab.core.config import get_settings
from voice_lab.core.database import engine as db_engine
from voice_lab.main import app
from voice_lab.modules.telephony import service as telephony_service

pytestmark = pytest.mark.integration
ADMIN = {"Authorization": "Bearer test-admin-token"}
LAB_NUMBER = "+15005550006"


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    asyncio.run(db_engine.dispose())
    with TestClient(app) as c:
        yield c
    asyncio.run(db_engine.dispose())


@pytest.fixture(autouse=True)
def no_watchers():  # type: ignore[no-untyped-def]
    with patch("voice_lab.modules.calls.inbound.ensure_watcher"), patch("voice_lab.modules.calls.service.ensure_watcher"):
        yield


def _signed(path: str, params: dict[str, str]) -> dict[str, str]:
    url = get_settings().public_base_url + path
    return {"X-Twilio-Signature": RequestValidator(get_settings().twilio_auth_token).compute_signature(url, params)}


def _inbound(client: TestClient, to: str = LAB_NUMBER, caller: str = "+34611222333", sid: str | None = None) -> ET.Element:
    params = {"CallSid": sid or "CA" + uuid.uuid4().hex, "From": caller, "To": to, "Direction": "inbound"}
    r = client.post("/twilio/voice", data=params, headers=_signed("/twilio/voice", params))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml"), r.text
    return ET.fromstring(r.text)


def _agent(client: TestClient, name: str, **extra) -> dict:  # type: ignore[no-untyped-def]
    body = {"name": name, "settings": {"instructions": f"Eres {name}.", "greeting": f"Hola, soy {name}."}, **extra}
    r = client.post("/api/agents", headers=ADMIN, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_voice_webhook_requires_twilio_signature(client: TestClient) -> None:
    r = client.post("/twilio/voice", data={"CallSid": "CA1", "From": "+1", "To": LAB_NUMBER}, headers={"X-Twilio-Signature": "bad"})
    assert r.status_code == 403


def test_inbound_routes_by_number_and_falls_back_to_default(client: TestClient) -> None:
    agent = _agent(client, "Recepción inbound", inbound_number="+34 910 000 001")
    assert agent["inbound_number"] == "+34910000001"

    twiml = _inbound(client, to="+34910000001")
    stream = twiml.find("./Connect/Stream")
    assert stream is not None and stream.get("url") == "wss://lab.example.test/twilio/media"
    call_id = {p.get("name"): p.get("value") for p in stream.iter("Parameter")}["call_id"]
    detail = client.get(f"/api/calls/{call_id}", headers=ADMIN).json()
    assert detail["call"]["direction"] == "inbound" and detail["call"]["config_snapshot"]["agent"]["name"] == "Recepción inbound"
    assert detail["call"]["from_number"] == "+34611222333" and detail["call"]["to_number"] == "+34910000001"
    assert {"call.created", "call.answered"} <= {e["name"] for e in detail["events"]}

    other = _inbound(client, to=LAB_NUMBER)  # no agent owns this number -> default agent
    other_id = {p.get("name"): p.get("value") for p in other.iter("Parameter")}["call_id"]
    snap = client.get(f"/api/calls/{other_id}", headers=ADMIN).json()["call"]["config_snapshot"]
    default = next(a for a in client.get("/api/agents", headers=ADMIN).json() if a["is_default"])
    assert snap["agent"]["id"] == default["id"]


def test_inbound_webhook_retry_reuses_the_call(client: TestClient) -> None:
    sid = "CA" + uuid.uuid4().hex
    first = {p.get("name"): p.get("value") for p in _inbound(client, sid=sid).iter("Parameter")}
    second = {p.get("name"): p.get("value") for p in _inbound(client, sid=sid).iter("Parameter")}
    assert first["call_id"] == second["call_id"] and first["token"] != second["token"]  # fresh single-use token


def test_inbound_allowlist_rejects_unknown_callers(client: TestClient) -> None:
    get_settings().inbound_allowlist = ["+34600000001"]
    try:
        assert _inbound(client, caller="+34699999999").find("./Reject") is not None
        assert _inbound(client, caller="+34600000001").find("./Connect/Stream") is not None
    finally:
        get_settings().inbound_allowlist = []


def test_inbound_apologises_instead_of_failing(client: TestClient) -> None:
    agent = _agent(
        client,
        "Agente Claude inbound",
        inbound_number="+34910000002",
        settings={"instructions": "x", "llm_provider": "anthropic", "llm_model": "claude-haiku-5-5"},
    )
    assert agent["inbound_number"] == "+34910000002"
    twiml = _inbound(client, to="+34910000002")  # ANTHROPIC_API_KEY is empty in tests
    assert twiml.find("./Say") is not None and twiml.find("./Hangup") is not None


def test_inbound_to_a_retell_agent_dials_retell_sip(client: TestClient) -> None:
    _agent(
        client,
        "Agente Retell inbound",
        inbound_number="+34910000003",
        voice_provider="retell",
        settings={"instructions": "x", "llm_provider": "anthropic", "llm_model": "claude-haiku-5-5"},
    )
    with (
        patch("voice_lab.integrations.voice.retell.client.RetellClient.create_retell_llm", new=AsyncMock(return_value={"llm_id": "llm_1"})),
        patch("voice_lab.integrations.voice.retell.client.RetellClient.create_agent", new=AsyncMock(return_value={"agent_id": "agent_1"})),
        patch(
            "voice_lab.integrations.voice.retell.client.RetellClient.register_phone_call", new=AsyncMock(return_value={"call_id": "rc_123"})
        ) as register,
    ):
        sid = "CA" + uuid.uuid4().hex
        twiml = _inbound(client, to="+34910000003", sid=sid)
        assert twiml.find("./Dial/Sip").text == "sip:rc_123@sip.retellai.com"
        assert twiml.find("./Dial").get("timeLimit") == str(get_settings().max_call_duration_seconds)
        assert register.await_args.kwargs["direction"] == "inbound" and register.await_args.kwargs["agent_id"] == "agent_1"
        again = _inbound(client, to="+34910000003", sid=sid)  # Twilio retry: no second registration
        assert again.find("./Dial/Sip").text == "sip:rc_123@sip.retellai.com" and register.await_count == 1


def test_outbound_through_retell_dials_sip(client: TestClient) -> None:
    with (
        patch("voice_lab.integrations.voice.retell.client.RetellClient.create_retell_llm", new=AsyncMock(return_value={"llm_id": "llm_2"})),
        patch("voice_lab.integrations.voice.retell.client.RetellClient.create_agent", new=AsyncMock(return_value={"agent_id": "agent_2"})),
        patch(
            "voice_lab.integrations.voice.retell.client.RetellClient.register_phone_call", new=AsyncMock(return_value={"call_id": "rc_out"})
        ),
        patch(
            "voice_lab.integrations.telephony.twilio.client.TwilioTelephony.start_outbound",
            new=AsyncMock(return_value="CA" + uuid.uuid4().hex),
        ) as start,
    ):
        r = client.post(
            "/api/calls",
            headers=ADMIN,
            json={
                "to": "+34600111222",
                "config": {"voice_provider": "retell", "llm_provider": "anthropic", "llm_model": "claude-haiku-5-5", "greeting": "Hola"},
            },
        )
    assert r.status_code == 201, r.text
    assert "<Sip>sip:rc_out@sip.retellai.com</Sip>" in start.await_args.kwargs["twiml"]
    detail = client.get(f"/api/calls/{r.json()['id']}", headers=ADMIN).json()
    assert detail["call"]["provider_refs"] == {"retell_call_id": "rc_out"} and detail["call"]["status"] == "initiated"


def test_agent_inbound_number_must_be_unique_and_valid(client: TestClient) -> None:
    _agent(client, "Número único", inbound_number="+34910000009")
    r = client.post("/api/agents", headers=ADMIN, json={"name": "Duplicado", "inbound_number": "+34910000009"})
    assert r.status_code == 409
    assert client.post("/api/agents", headers=ADMIN, json={"name": "Malo", "inbound_number": "910"}).status_code == 422


class FakeTwilio:
    def __init__(self, voice_url: str | None = None, exists: bool = True) -> None:
        self.voice_url, self.exists, self.configured = voice_url, exists, None

    async def account_status(self) -> str:
        return "active"

    async def find_number(self, phone: str):  # type: ignore[no-untyped-def]
        if not self.exists:
            return None
        return {
            "sid": "PN1",
            "phone_number": phone,
            "voice_url": self.voice_url,
            "voice_method": "POST",
            "status_callback": self.voice_url and self.voice_url.replace("voice", "status"),
            "voice_capable": True,
        }

    async def configure_number(self, sid: str, voice_url: str, status_callback: str) -> None:
        self.configured = (sid, voice_url, status_callback)
        self.voice_url = voice_url


async def test_status_and_configure_with_fake_twilio() -> None:
    from voice_lab.core.database import SessionLocal

    settings = get_settings()
    fake = FakeTwilio()
    with patch.object(telephony_service, "_public_url_reaches_us", new=AsyncMock(return_value=(True, "reaches this API"))):
        async with SessionLocal() as db:
            before = await telephony_service.telephony_status(db, settings, fake)
        assert before["ready"] == {"outbound": True, "inbound": False}
        assert {c["key"]: c["ok"] for c in before["checks"]}["voice_webhook"] is False

        result = await telephony_service.configure_number(settings, fake)
        assert fake.configured == ("PN1", "https://lab.example.test/twilio/voice", "https://lab.example.test/twilio/status")
        assert result["voice_url"].endswith("/twilio/voice")
        async with SessionLocal() as db:
            after = await telephony_service.telephony_status(db, settings, fake)
        assert after["ready"] == {"outbound": True, "inbound": True}


async def test_configure_fails_clearly_when_number_is_missing() -> None:
    from voice_lab.core.errors import LabError

    with pytest.raises(LabError, match="not in this Twilio account"):
        await telephony_service.configure_number(get_settings(), FakeTwilio(exists=False))
