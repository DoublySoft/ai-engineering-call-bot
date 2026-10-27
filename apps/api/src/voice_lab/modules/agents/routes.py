import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.core.config import Settings, get_settings
from voice_lab.core.database import get_db
from voice_lab.core.errors import LabError
from voice_lab.core.security import require_admin
from voice_lab.modules.agents import service
from voice_lab.modules.agents.prompt import VOICE_RULES
from voice_lab.modules.agents.schemas import LANGUAGES, AgentDetail, AgentOut, AgentWrite

router = APIRouter(prefix="/api/agents", tags=["agents"], dependencies=[Depends(require_admin)])


def _http(exc: LabError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


@router.get("")
async def list_agents(include_archived: bool = False, db: AsyncSession = Depends(get_db)) -> list[AgentOut]:
    return await service.list_agents(db, include_archived)


@router.get("/meta")
async def agent_meta() -> dict:
    """Static info the agent editor shows: languages and the fixed voice rules."""
    return {"languages": LANGUAGES, "voice_rules": VOICE_RULES}


@router.post("", status_code=201)
async def create_agent(data: AgentWrite, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> AgentDetail:
    try:
        agent_id = await service.create_agent(db, data, settings)
        return await service.agent_detail(db, agent_id, settings)
    except LabError as exc:
        raise _http(exc) from exc


@router.get("/{agent_id}")
async def get_agent(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> AgentDetail:
    try:
        return await service.agent_detail(db, agent_id, settings)
    except LabError as exc:
        raise _http(exc) from exc


@router.put("/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID, data: AgentWrite, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> AgentDetail:
    try:
        await service.update_agent(db, agent_id, data, settings)
        return await service.agent_detail(db, agent_id, settings)
    except LabError as exc:
        raise _http(exc) from exc


@router.post("/{agent_id}/default")
async def make_default(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        await service.set_default(db, agent_id)
    except LabError as exc:
        raise _http(exc) from exc
    return {"status": "default"}


@router.delete("/{agent_id}")
async def archive(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        await service.archive_agent(db, agent_id)
    except LabError as exc:
        raise _http(exc) from exc
    return {"status": "archived"}
