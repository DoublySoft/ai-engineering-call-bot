import asyncio

import pytest
from fastapi.testclient import TestClient

from voice_lab.core.database import engine as db_engine
from voice_lab.main import app

pytestmark = pytest.mark.integration
ADMIN = {"Authorization": "Bearer test-admin-token"}

RECEPTIONIST = {
    "name": "Recepción clínica",
    "description": "Atiende llamadas de una clínica dental",
    "settings": {
        "instructions": "Eres la recepcionista de la Clínica Dental Sonrisa. Ayuda a pedir cita.",
        "knowledge": "Horario: L-V 9-18 h. Dirección: Calle Mayor 1.",
        "greeting": "Clínica Sonrisa, ¿dígame?",
        "language": "es",
        "tts_voice": "nova",
    },
}


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    asyncio.run(db_engine.dispose())
    with TestClient(app) as c:
        yield c
    asyncio.run(db_engine.dispose())


def _create(client: TestClient, **changes) -> dict:  # type: ignore[no-untyped-def]
    body = {**RECEPTIONIST, **changes}
    r = client.post("/api/agents", headers=ADMIN, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_agents_require_admin(client: TestClient) -> None:
    assert client.get("/api/agents").status_code == 401


def test_default_agent_always_exists(client: TestClient) -> None:
    agents = client.get("/api/agents", headers=ADMIN).json()
    assert sum(a["is_default"] for a in agents) == 1


def test_create_and_detail_shows_effective_prompt(client: TestClient) -> None:
    agent = _create(client, name="Recepción A")
    assert agent["version"] == 1 and len(agent["versions"]) == 1
    assert "Calle Mayor 1" in agent["effective"]["system_prompt"]
    assert agent["effective"]["stt_model"]  # server default filled in


def test_update_creates_versions_only_when_behaviour_changes(client: TestClient) -> None:
    agent = _create(client, name="Recepción B")
    url = f"/api/agents/{agent['id']}"
    same = client.put(url, headers=ADMIN, json={**RECEPTIONIST, "name": "Recepción B (renombrada)"}).json()
    assert same["version"] == 1 and same["name"] == "Recepción B (renombrada)"
    changed = {**RECEPTIONIST, "name": "Recepción B", "settings": {**RECEPTIONIST["settings"], "greeting": "Buenas, Clínica Sonrisa."}}
    updated = client.put(url, headers=ADMIN, json=changed).json()
    assert updated["version"] == 2 and [v["version"] for v in updated["versions"]] == [2, 1]


def test_validation_errors(client: TestClient) -> None:
    _create(client, name="Recepción C")
    assert client.post("/api/agents", headers=ADMIN, json={**RECEPTIONIST, "name": "recepción c"}).status_code == 409
    bad_model = {**RECEPTIONIST, "name": "X", "settings": {**RECEPTIONIST["settings"], "llm_model": "nope"}}
    assert client.post("/api/agents", headers=ADMIN, json=bad_model).status_code == 400
    bad_s2s = {**RECEPTIONIST, "name": "Y", "engine": "realtime", "settings": {**RECEPTIONIST["settings"], "s2s_model": "tts-1"}}
    assert client.post("/api/agents", headers=ADMIN, json=bad_s2s).status_code == 400


def test_session_uses_agent_and_records_version(client: TestClient) -> None:
    agent = _create(client, name="Recepción D")
    r = client.post("/api/browser-sessions", headers=ADMIN, json={"config": {"agent_id": agent["id"], "llm_model": "gpt-4o-mini"}})
    assert r.status_code == 201, r.text
    detail = client.get(f"/api/calls/{r.json()['id']}", headers=ADMIN).json()
    snap = detail["call"]["config_snapshot"]
    assert snap["agent"] == {"id": agent["id"], "name": "Recepción D", "version": 1, "configuration_id": agent["configuration_id"]}
    assert snap["llm_model"] == "gpt-4o-mini"  # per-call override
    assert snap["tts_voice"] == "nova" and snap["greeting"] == "Clínica Sonrisa, ¿dígame?"
    assert "Calle Mayor 1" in snap["system_prompt"]
    assert detail["call"]["agent_configuration_id"] == agent["configuration_id"]


def test_default_and_archive(client: TestClient) -> None:
    agent = _create(client, name="Recepción E")
    url = f"/api/agents/{agent['id']}"
    assert client.post(f"{url}/default", headers=ADMIN).status_code == 200
    assert client.delete(url, headers=ADMIN).status_code == 400  # default can't be archived
    other = _create(client, name="Recepción F")
    client.post(f"/api/agents/{other['id']}/default", headers=ADMIN)
    assert client.delete(url, headers=ADMIN).status_code == 200
    names = [a["name"] for a in client.get("/api/agents", headers=ADMIN).json()]
    assert "Recepción E" not in names
    r = client.post("/api/browser-sessions", headers=ADMIN, json={"config": {"agent_id": agent["id"]}})
    assert r.status_code == 400 and "archived" in r.json()["detail"]
    # Sessions without agent_id use the new default.
    call_id = client.post("/api/browser-sessions", headers=ADMIN, json={}).json()["id"]
    assert client.get(f"/api/calls/{call_id}", headers=ADMIN).json()["call"]["config_snapshot"]["agent"]["name"] == "Recepción F"


def test_agent_with_legacy_language_stays_usable(client: TestClient) -> None:
    import uuid

    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from voice_lab.core.config import get_settings
    from voice_lab.persistence.models import VoiceAgentConfiguration

    agent = _create(client, name="Recepción legado")
    own = async_sessionmaker(create_async_engine(get_settings().database_url, poolclass=NullPool))

    async def make_legacy() -> None:  # simulate a version stored before languages were validated
        async with own() as db:
            cfg_id = uuid.UUID(agent["configuration_id"])
            row = await db.get(VoiceAgentConfiguration, cfg_id)
            await db.execute(
                update(VoiceAgentConfiguration)
                .where(VoiceAgentConfiguration.id == cfg_id)
                .values(settings={**row.settings, "language": "Japanese"})
            )
            await db.commit()

    asyncio.run(make_legacy())
    assert client.get("/api/agents", headers=ADMIN).status_code == 200
    assert client.get(f"/api/agents/{agent['id']}", headers=ADMIN).json()["settings"]["language"] == "Japanese"
    r = client.post("/api/browser-sessions", headers=ADMIN, json={"config": {"agent_id": agent["id"]}})
    assert r.status_code == 201, r.text
