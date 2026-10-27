import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from voice_lab.core.config import get_settings
from voice_lab.core.database import SessionLocal
from voice_lab.core.errors import LabError
from voice_lab.integrations.voice.retell.client import RetellClient
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.retell import service
from voice_lab.modules.sessions.config import resolve_config
from voice_lab.persistence.models import Call, ConversationMessage, ConversationSession, ProviderAgentBinding, UsageEvent

pytestmark = pytest.mark.integration


class FakeRetell:
    """httpx MockTransport handler emulating the Retell endpoints the lab uses."""

    def __init__(self) -> None:
        self.calls: dict[str, dict] = {}
        self.created: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        if path == "/create-retell-llm":
            self.created.append((path, body))
            return httpx.Response(201, json={"llm_id": f"llm_{len(self.created)}", "version": 0})
        if path == "/create-agent":
            self.created.append((path, body))
            return httpx.Response(201, json={"agent_id": f"agent_{len(self.created)}", "version": 0})
        if path == "/v3/create-web-call":
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            self.calls[call_id] = {"call_status": "registered", "call_cost": {"combined_cost": 0, "product_costs": []}}
            return httpx.Response(201, json={"call_id": call_id, "access_token": "tok", "transport": "gateway", "ice_servers": []})
        if path.startswith("/v2/get-call/"):
            return httpx.Response(200, json=self.calls[path.rsplit("/", 1)[1]])
        return httpx.Response(404, json={"message": "nope"})


def _client(fake: FakeRetell) -> RetellClient:
    return RetellClient("key_test", http=httpx.AsyncClient(base_url="https://api.retellai.com", transport=httpx.MockTransport(fake)))


async def _session(llm_model: str = "claude-haiku-5-5", greeting: str | None = "Hola") -> uuid.UUID:
    config = resolve_config(
        SessionConfig(voice_provider="retell", llm_provider="anthropic", llm_model=llm_model, greeting=greeting), get_settings()
    )
    config["agent"] = {"name": "Test", "version": 1}
    async with SessionLocal() as db:
        call = Call(direction="browser", status="ready", config_snapshot=config, pricing_snapshot={"retell": "provider_reported"})
        db.add(call)
        await db.commit()
        return call.id


async def _start(fake: FakeRetell, call_id: uuid.UUID) -> dict:
    async with SessionLocal() as db:
        return await service.start_web_call(db, call_id, _client(fake), get_settings())


async def test_agent_is_created_once_per_identical_setup() -> None:
    fake = FakeRetell()
    a, b = await _session(), await _session()
    web = await _start(fake, a)
    await _start(fake, b)
    assert web["access_token"] == "tok" and web["transport"] == "gateway"
    assert [p for p, _ in fake.created] == ["/create-retell-llm", "/create-agent"]  # second session reused it
    llm_body, agent_body = fake.created[0][1], fake.created[1][1]
    assert llm_body["model_config"] == {"type": "cascading", "primary": {"model": "claude-5.5-haiku"}}
    assert llm_body["begin_message"] == "Hola" and llm_body["start_speaker"] == "agent" and "# Tu papel" in llm_body["general_prompt"]
    assert agent_body["language"] == "es-ES" and agent_body["data_storage_setting"] == "basic_attributes_only"
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(ProviderAgentBinding)) >= 1
        call = await db.get(Call, a)
        assert call.status == "in-progress" and call.provider_call_id == web["call_id"]


async def test_different_setup_gets_its_own_agent() -> None:
    fake = FakeRetell()
    await _start(fake, await _session(greeting="Uno"))
    await _start(fake, await _session(greeting="Dos"))
    assert [p for p, _ in fake.created].count("/create-agent") == 2


async def test_sync_imports_cost_latency_and_retell_transcript_only_after_end() -> None:
    fake = FakeRetell()
    call_id = await _session()
    web = await _start(fake, call_id)
    client = _client(fake)
    early = await service.sync_call(call_id, client)
    assert early["call_status"] == "registered" and early["cost_available"] is False  # the 0 cost is not imported

    fake.calls[web["call_id"]] = {
        "call_status": "ended",
        "start_timestamp": 1_793_000_000_000,
        "end_timestamp": 1_793_000_042_000,
        "duration_ms": 42_000,
        "disconnection_reason": "user_hangup",
        "transcript_object": [
            {"role": "agent", "content": "Hola"},
            {"role": "user", "content": "¿Abrís hoy?"},
            {"role": "agent", "content": "Sí."},
        ],
        "call_cost": {"combined_cost": 12.5, "total_duration_seconds": 42, "product_costs": [{"product": "retell_platform", "cost": 5}]},
        "latency": {"e2e": {"p50": 900}, "llm": {"p50": 400}, "asr": {"p50": 150}, "tts": {"p50": 200}},
    }
    done = await service.sync_call(call_id, client, client_transcript=[{"role": "user", "content": "ignored"}])
    assert done == {"call_status": "ended", "cost_available": True, "transcript_source": "retell"}
    await service.sync_call(call_id, client)  # idempotent

    async with SessionLocal() as db:
        call = await db.get(Call, call_id)
        session = await db.scalar(select(ConversationSession).where(ConversationSession.call_id == call_id))
        roles = (
            await db.scalars(
                select(ConversationMessage.role).where(ConversationMessage.session_id == session.id).order_by(ConversationMessage.seq)
            )
        ).all()
        usage = (await db.scalars(select(UsageEvent).where(UsageEvent.call_id == call_id))).all()
    assert call.status == "completed" and call.duration_seconds == 42
    assert roles == ["agent", "user", "agent"]
    assert len(usage) == 1 and usage[0].operation_type == "managed" and str(usage[0].cost_usd) == "0.12500000"
    assert usage[0].cost_status == "provider_reported"
    assert session.metrics["avg_time_to_first_audio_ms"] == 900 and session.metrics["turns"] == 1


async def test_browser_transcript_used_when_retell_keeps_none() -> None:
    fake = FakeRetell()
    call_id = await _session()
    web = await _start(fake, call_id)
    fake.calls[web["call_id"]] = {"call_status": "ended", "duration_ms": 5000, "call_cost": {"combined_cost": 1}}
    result = await service.sync_call(call_id, _client(fake), [{"role": "agent", "content": "Hola"}, {"role": "user", "content": "Adiós"}])
    assert result["transcript_source"] == "browser"


async def test_guards() -> None:
    fake = FakeRetell()
    call_id = await _session()
    await _start(fake, call_id)
    with pytest.raises(LabError, match="in-progress"):
        await _start(fake, call_id)  # one web call per session
    async with SessionLocal() as db:
        db.add(Call(direction="browser", status="ready", config_snapshot=resolve_config(SessionConfig(), get_settings())))
        await db.commit()
        pipeline_id = (await db.scalars(select(Call.id).order_by(Call.created_at.desc()))).first()
    with pytest.raises(LabError, match="doesn't use Retell"):
        await _start(fake, pipeline_id)
    await asyncio.sleep(0)


async def _ended(fake: FakeRetell) -> uuid.UUID:
    call_id = await _session()
    web = await _start(fake, call_id)
    fake.calls[web["call_id"]] = {
        "call_status": "ended",
        "duration_ms": 30_000,
        "call_cost": {"combined_cost": 7, "total_duration_seconds": 30},
    }
    return call_id


async def test_concurrent_syncs_import_cost_once() -> None:
    fake = FakeRetell()
    call_id = await _ended(fake)
    results = await asyncio.gather(*(service.sync_call(call_id, _client(fake)) for _ in range(5)), return_exceptions=True)
    assert not [r for r in results if isinstance(r, Exception)]
    async with SessionLocal() as db:
        managed = (await db.scalars(select(UsageEvent).where(UsageEvent.call_id == call_id, UsageEvent.operation_type == "managed"))).all()
    assert len(managed) == 1 and str(managed[0].cost_usd) == "0.07000000"


async def test_watcher_finalises_without_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "WATCH_INTERVAL_S", 0.01)
    fake = FakeRetell()
    call_id = await _session()
    web = await _start(fake, call_id)
    client = _client(fake)
    service.remember_browser_transcript(call_id, [{"role": "user", "content": "Hola"}])
    service.ensure_watcher(call_id, client, max_call_s=5)
    task = service._watchers[call_id]
    service.ensure_watcher(call_id, client, max_call_s=5)
    assert service._watchers[call_id] is task  # one watcher per call
    await asyncio.sleep(0.05)  # call still ongoing: watcher keeps polling
    assert not task.done()
    fake.calls[web["call_id"]] = {"call_status": "ended", "duration_ms": 1000, "call_cost": {"combined_cost": 2}}
    await asyncio.wait_for(task, timeout=2)
    async with SessionLocal() as db:
        call = await db.get(Call, call_id)
        roles = (
            await db.scalars(select(ConversationMessage.role).join(ConversationSession).where(ConversationSession.call_id == call_id))
        ).all()
    assert call.status == "completed" and roles == ["user"]  # browser transcript kept for the watcher
    assert call_id not in service._watchers
