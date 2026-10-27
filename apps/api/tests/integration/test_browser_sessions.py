import asyncio
import json
import time
import uuid
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.fakes import FakeLLM, FakeSTT, FakeTTS, silence, speech
from voice_lab.core.config import get_settings
from voice_lab.core.database import engine as db_engine
from voice_lab.main import app
from voice_lab.persistence.models import Call, CallEvent, ConversationMessage, ConversationSession, UsageEvent

pytestmark = pytest.mark.integration
ADMIN = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    asyncio.run(db_engine.dispose())
    with TestClient(app) as c:
        yield c
    asyncio.run(db_engine.dispose())


def _own_sessionmaker():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(get_settings().database_url, poolclass=NullPool), expire_on_commit=False)


def test_browser_session_requires_llm_provider_key(client: TestClient) -> None:
    r = client.post("/api/browser-sessions", headers=ADMIN, json={"config": {"llm_provider": "anthropic", "llm_model": "claude-haiku-5-5"}})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_browser_token_is_admin_only_and_single_session(client: TestClient) -> None:
    call_id = client.post("/api/browser-sessions", headers=ADMIN, json={}).json()["id"]
    assert client.post(f"/api/calls/{call_id}/browser-token").status_code == 401
    assert client.post(f"/api/calls/{call_id}/browser-token", headers=ADMIN).status_code == 200


def test_browser_ws_rejects_bad_token(client: TestClient) -> None:
    call_id = client.post("/api/browser-sessions", headers=ADMIN, json={}).json()["id"]
    client.post(f"/api/calls/{call_id}/browser-token", headers=ADMIN)
    with client.websocket_connect(f"/browser/audio?call_id={call_id}&token=wrong") as ws:
        assert ws.receive()["type"] == "websocket.close"


def _recv_until_mark(ws) -> int:  # type: ignore[no-untyped-def]
    audio_bytes = 0
    while True:
        msg = ws.receive()
        if msg.get("bytes"):
            audio_bytes += len(msg["bytes"])
        elif msg.get("text"):
            control = json.loads(msg["text"])
            if control["type"] == "mark":
                ws.send_text(json.dumps({"type": "mark", "name": control["name"]}))  # page finished playing
                return audio_bytes


def test_browser_conversation_end_to_end(client: TestClient) -> None:
    created = client.post("/api/browser-sessions", headers=ADMIN, json={"config": {"greeting": "Hola"}})
    assert created.status_code == 201, created.text
    call_id = created.json()["id"]
    token = client.post(f"/api/calls/{call_id}/browser-token", headers=ADMIN).json()

    mic = np.concatenate([silence(200, rate=16000), speech(600, rate=16000), silence(1000, rate=16000)]).astype("<i2")
    with (
        patch("voice_lab.integrations.registry.stt_client", return_value=FakeSTT()),
        patch("voice_lab.integrations.registry.llm_client", return_value=FakeLLM()),
        patch("voice_lab.integrations.registry.tts_client", return_value=FakeTTS()),
    ):
        with client.websocket_connect(token["path"]) as ws:
            greeting_bytes = _recv_until_mark(ws)
            for i in range(0, mic.size, 320):  # 20 ms frames at 16 kHz
                ws.send_bytes(mic[i : i + 320].tobytes())
            reply_bytes = _recv_until_mark(ws)
            ws.send_text(json.dumps({"type": "stop"}))
            assert ws.receive()["type"] == "websocket.close"
    # FakeTTS yields 100 ms at 24 kHz per sentence -> 4800 bytes of PCM16
    assert greeting_bytes == 4800 and reply_bytes == 2 * 4800

    # A finished session cannot be reconnected.
    assert client.post(f"/api/calls/{call_id}/browser-token", headers=ADMIN).status_code == 409

    own = _own_sessionmaker()

    async def check():  # type: ignore[no-untyped-def]
        async with own() as db:
            call = await db.get(Call, uuid.UUID(call_id))
            session = await db.scalar(select(ConversationSession).where(ConversationSession.call_id == call.id))
            roles = (
                await db.scalars(
                    select(ConversationMessage.role).where(ConversationMessage.session_id == session.id).order_by(ConversationMessage.seq)
                )
            ).all()
            names = (await db.scalars(select(CallEvent.name).where(CallEvent.call_id == call.id))).all()
            ops = (await db.scalars(select(UsageEvent.operation_type).where(UsageEvent.call_id == call.id))).all()
            return call, session, list(roles), list(names), set(ops)

    for _ in range(40):
        call, session, roles, names, ops = asyncio.run(check())
        if call.status == "completed":
            break
        time.sleep(0.05)
    assert call.status == "completed" and call.duration_seconds is not None and session.status == "completed"
    assert roles == ["agent", "user", "agent"]
    assert {"call.created", "call.answered", "audio.playback.completed", "session.completed", "call.completed"} <= set(names)
    assert ops == {"stt", "llm", "tts"}  # no telephony cost in browser mode
