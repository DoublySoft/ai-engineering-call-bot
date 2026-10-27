import base64
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from twilio.request_validator import RequestValidator

from tests.fakes import FakeLLM, FakeSTT, FakeTTS, silence, speech
from voice_lab.audio.codec import pcm16_to_mulaw
from voice_lab.core.config import get_settings
from voice_lab.core.database import engine as db_engine
from voice_lab.main import app
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.sessions.config import resolve_config
from voice_lab.persistence.models import Call, CallEvent, ConversationMessage, ConversationSession

pytestmark = pytest.mark.integration
ADMIN = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    # TestClient runs the app on its own loop; drop pooled connections from other loops.
    import asyncio

    asyncio.run(db_engine.dispose())
    with TestClient(app) as c:
        yield c
    asyncio.run(db_engine.dispose())


def _signed(path: str, params: dict[str, str]) -> dict[str, str]:
    url = get_settings().public_base_url + path
    return {"X-Twilio-Signature": RequestValidator(get_settings().twilio_auth_token).compute_signature(url, params)}


def test_admin_endpoints_require_token(client: TestClient) -> None:
    assert client.get("/api/calls").status_code == 401
    assert client.get("/api/calls", headers=ADMIN).status_code == 200


def test_outbound_call_uses_twilio_with_stream_twiml(client: TestClient) -> None:
    with patch(
        "voice_lab.integrations.telephony.twilio.client.TwilioTelephony.start_outbound", new=AsyncMock(return_value="CA" + "1" * 32)
    ) as start:
        r = client.post("/api/calls", headers=ADMIN, json={"to": "+34600111222"})
    assert r.status_code == 201, r.text
    kwargs = start.await_args.kwargs
    assert kwargs["to"] == "+34600111222"
    assert "wss://lab.example.test/twilio/media" in kwargs["twiml"] and r.json()["id"] in kwargs["twiml"]
    assert kwargs["status_callback"] == "https://lab.example.test/twilio/status"
    detail = client.get(f"/api/calls/{r.json()['id']}", headers=ADMIN).json()
    assert detail["call"]["status"] == "initiated" and "stream_token" not in detail["call"]
    assert detail["call"]["pricing_snapshot"]["llm"]["verified"] is True


def test_outbound_rejects_non_allowlisted(client: TestClient) -> None:
    r = client.post("/api/calls", headers=ADMIN, json={"to": "+34600999999"})
    assert r.status_code == 403


def test_twilio_failure_marks_call_failed(client: TestClient) -> None:
    with patch(
        "voice_lab.integrations.telephony.twilio.client.TwilioTelephony.start_outbound", new=AsyncMock(side_effect=RuntimeError("boom"))
    ):
        r = client.post("/api/calls", headers=ADMIN, json={"to": "+34600111222"})
    assert r.status_code == 502
    calls = client.get("/api/calls", headers=ADMIN).json()
    assert calls[0]["status"] == "failed" and "boom" in calls[0]["error"]


def test_status_webhook_rejects_bad_signature(client: TestClient) -> None:
    r = client.post("/twilio/status", data={"CallSid": "CAx", "CallStatus": "ringing"}, headers={"X-Twilio-Signature": "nope"})
    assert r.status_code == 403


def test_status_webhook_updates_call(client: TestClient) -> None:
    sid = "CA" + uuid.uuid4().hex
    with patch("voice_lab.integrations.telephony.twilio.client.TwilioTelephony.start_outbound", new=AsyncMock(return_value=sid)):
        call_id = client.post("/api/calls", headers=ADMIN, json={"to": "+34600111222"}).json()["id"]
    for status, extra in (("ringing", {}), ("in-progress", {}), ("completed", {"CallDuration": "42"})):
        params = {"CallSid": sid, "CallStatus": status, **extra}
        with patch("voice_lab.modules.calls.service.record_telephony_cost", new=AsyncMock()):
            r = client.post("/twilio/status", data=params, headers=_signed("/twilio/status", params))
        assert r.status_code == 204
    detail = client.get(f"/api/calls/{call_id}", headers=ADMIN).json()
    assert detail["call"]["status"] == "completed" and detail["call"]["duration_seconds"] == 42
    names = [e["name"] for e in detail["events"]]
    assert {"call.created", "call.ringing", "call.answered", "call.completed"} <= set(names)


def test_media_stream_rejects_bad_token(client: TestClient) -> None:
    with client.websocket_connect("/twilio/media") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(
            json.dumps(
                {
                    "event": "start",
                    "start": {"streamSid": "MZ1", "callSid": "CA1", "customParameters": {"call_id": str(uuid.uuid4()), "token": "x"}},
                }
            )
        )
        with pytest.raises(Exception):
            ws.receive_text()


def test_media_stream_runs_a_conversation(client: TestClient) -> None:
    import asyncio

    config = resolve_config(SessionConfig(greeting="Hola"), get_settings())

    # Own NullPool engine: the app's pool lives on the TestClient's loop.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    own = async_sessionmaker(create_async_engine(get_settings().database_url, poolclass=NullPool), expire_on_commit=False)

    async def seed() -> uuid.UUID:
        async with own() as db:
            call = Call(
                direction="outbound",
                status="in-progress",
                config_snapshot=config,
                stream_token="tok123",
                provider_call_id="CA" + uuid.uuid4().hex,
            )
            db.add(call)
            await db.commit()
            return call.id

    call_id = asyncio.run(seed())

    audio = pcm16_to_mulaw(__import__("numpy").concatenate([silence(200), speech(600), silence(1000)]))
    with (
        patch("voice_lab.integrations.registry.stt_client", return_value=FakeSTT()),
        patch("voice_lab.integrations.registry.llm_client", return_value=FakeLLM()),
        patch("voice_lab.integrations.registry.tts_client", return_value=FakeTTS()),
    ):
        with client.websocket_connect("/twilio/media") as ws:
            ws.send_text(json.dumps({"event": "connected"}))
            ws.send_text(
                json.dumps(
                    {
                        "event": "start",
                        "start": {"streamSid": "MZ1", "callSid": "CA1", "customParameters": {"call_id": str(call_id), "token": "tok123"}},
                    }
                )
            )
            outbound_media = 0
            # greeting audio arrives first
            while True:
                msg = json.loads(ws.receive_text())
                if msg["event"] == "media":
                    outbound_media += 1
                if msg["event"] == "mark":
                    break
            for i in range(0, len(audio), 160):
                ws.send_text(
                    json.dumps({"event": "media", "media": {"track": "inbound", "payload": base64.b64encode(audio[i : i + 160]).decode()}})
                )
            # wait for the agent reply's mark
            while True:
                msg = json.loads(ws.receive_text())
                if msg["event"] == "mark":
                    break
            ws.send_text(json.dumps({"event": "stop"}))
            assert ws.receive()["type"] == "websocket.close"  # server closes after finalizing
        assert outbound_media > 0

    async def check() -> tuple[list[str], str, list[str]]:
        async with own() as db:
            msgs = (
                await db.scalars(
                    select(ConversationMessage.role)
                    .join(ConversationSession)
                    .where(ConversationSession.call_id == call_id)
                    .order_by(ConversationMessage.seq)
                )
            ).all()
            session = await db.scalar(select(ConversationSession).where(ConversationSession.call_id == call_id))
            names = (await db.scalars(select(CallEvent.name).where(CallEvent.call_id == call_id))).all()
            return list(msgs), session.status, list(names)

    import time

    # The server finalizes the session just after the socket closes.
    for _ in range(40):
        roles, status, names = asyncio.run(check())
        if status == "completed":
            break
        time.sleep(0.05)
    assert roles == ["agent", "user", "agent"]
    assert status == "completed" and "session.completed" in names
