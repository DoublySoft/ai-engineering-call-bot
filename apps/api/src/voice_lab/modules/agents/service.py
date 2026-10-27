import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.core.config import Settings
from voice_lab.core.errors import LabError
from voice_lab.modules.agents.schemas import AgentDetail, AgentOut, AgentSettings, AgentVersion, AgentWrite
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.sessions.config import base_from_configuration, resolve_config
from voice_lab.persistence.models import Call, VoiceAgent, VoiceAgentConfiguration

DEFAULT_AGENT_NAME = "Asistente del laboratorio"


class AgentError(LabError):
    pass


async def latest_configuration(db: AsyncSession, agent_id: uuid.UUID) -> VoiceAgentConfiguration | None:
    return await db.scalar(
        select(VoiceAgentConfiguration)
        .where(VoiceAgentConfiguration.agent_id == agent_id)
        .order_by(VoiceAgentConfiguration.version.desc())
        .limit(1)
    )


async def ensure_default_agent(db: AsyncSession) -> VoiceAgent:
    """There is always exactly one default, non-archived agent with at least one version."""
    agent = await db.scalar(select(VoiceAgent).where(VoiceAgent.is_default.is_(True), VoiceAgent.archived.is_(False)))
    if agent is None:
        agent = await db.scalar(select(VoiceAgent).where(VoiceAgent.archived.is_(False)).order_by(VoiceAgent.created_at))
        if agent is None:
            agent = VoiceAgent(name=DEFAULT_AGENT_NAME, description="Agente genérico creado automáticamente.")
            db.add(agent)
            await db.flush()
        agent.is_default = True
    if await latest_configuration(db, agent.id) is None:
        db.add(
            VoiceAgentConfiguration(
                agent_id=agent.id, version=1, voice_provider="custom", engine="pipeline", settings=AgentSettings().model_dump()
            )
        )
    await db.flush()
    return agent


async def get_usable_agent(db: AsyncSession, agent_id: uuid.UUID | None) -> tuple[VoiceAgent, VoiceAgentConfiguration]:
    if agent_id is None:
        agent = await ensure_default_agent(db)
    else:
        agent = await db.get(VoiceAgent, agent_id)
        if agent is None:
            raise AgentError("agent not found", 404)
        if agent.archived:
            raise AgentError(f"agent '{agent.name}' is archived")
    cfg = await latest_configuration(db, agent.id)
    if cfg is None:
        raise AgentError(f"agent '{agent.name}' has no configuration")
    return agent, cfg


def _out(agent: VoiceAgent, cfg: VoiceAgentConfiguration, calls: int = 0) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "is_default": agent.is_default,
        "archived": agent.archived,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "version": cfg.version,
        "configuration_id": cfg.id,
        "engine": cfg.engine,
        "voice_provider": cfg.voice_provider,
        "settings": AgentSettings.model_validate(cfg.settings),
        "calls": calls,
    }


async def _call_counts(db: AsyncSession) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(VoiceAgentConfiguration.agent_id, func.count(Call.id))
        .join(Call, Call.agent_configuration_id == VoiceAgentConfiguration.id)
        .group_by(VoiceAgentConfiguration.agent_id)
    )
    return dict(rows.all())


async def list_agents(db: AsyncSession, include_archived: bool = False) -> list[AgentOut]:
    await ensure_default_agent(db)
    await db.commit()
    query = select(VoiceAgent).order_by(VoiceAgent.is_default.desc(), VoiceAgent.name)
    if not include_archived:
        query = query.where(VoiceAgent.archived.is_(False))
    counts = await _call_counts(db)
    out = []
    for agent in (await db.scalars(query)).all():
        cfg = await latest_configuration(db, agent.id)
        if cfg:
            out.append(AgentOut.model_validate(_out(agent, cfg, counts.get(agent.id, 0))))
    return out


async def agent_detail(db: AsyncSession, agent_id: uuid.UUID, settings: Settings) -> AgentDetail:
    agent = await db.get(VoiceAgent, agent_id)
    if agent is None:
        raise AgentError("agent not found", 404)
    versions = (
        await db.scalars(
            select(VoiceAgentConfiguration)
            .where(VoiceAgentConfiguration.agent_id == agent_id)
            .order_by(VoiceAgentConfiguration.version.desc())
        )
    ).all()
    if not versions:
        raise AgentError("agent has no configuration")
    try:
        effective = resolve_config(SessionConfig(), settings, base_from_configuration(versions[0]))
    except LabError as exc:  # e.g. a model removed from the catalog since this version was saved
        effective = {"error": str(exc)}
    counts = await _call_counts(db)
    return AgentDetail.model_validate(
        {
            **_out(agent, versions[0], counts.get(agent.id, 0)),
            "versions": [AgentVersion(configuration_id=v.id, version=v.version, created_at=v.created_at) for v in versions],
            "effective": effective,
        }
    )


def _validate_runnable(data: AgentWrite, settings: Settings) -> None:
    """Reject agents the lab can't run (unknown model, unimplemented engine...)."""
    try:
        resolve_config(
            SessionConfig(), settings, {"engine": data.engine, "voice_provider": data.voice_provider, **data.settings.model_dump()}
        )
    except LabError as exc:
        raise AgentError(str(exc)) from exc


async def _name_taken(db: AsyncSession, name: str, exclude: uuid.UUID | None = None) -> bool:
    query = select(VoiceAgent.id).where(func.lower(VoiceAgent.name) == name.lower())
    if exclude:
        query = query.where(VoiceAgent.id != exclude)
    return await db.scalar(query) is not None


async def create_agent(db: AsyncSession, data: AgentWrite, settings: Settings) -> uuid.UUID:
    _validate_runnable(data, settings)
    if await _name_taken(db, data.name):
        raise AgentError(f"an agent named '{data.name}' already exists", 409)
    agent = VoiceAgent(name=data.name, description=data.description)
    db.add(agent)
    await db.flush()
    db.add(
        VoiceAgentConfiguration(
            agent_id=agent.id, version=1, voice_provider=data.voice_provider, engine=data.engine, settings=data.settings.model_dump()
        )
    )
    await db.commit()
    return agent.id


async def update_agent(db: AsyncSession, agent_id: uuid.UUID, data: AgentWrite, settings: Settings) -> bool:
    """Name/description change in place; anything that affects a conversation creates a new version.
    Returns True when a new version was created."""
    agent = await db.get(VoiceAgent, agent_id)
    if agent is None:
        raise AgentError("agent not found", 404)
    _validate_runnable(data, settings)
    if await _name_taken(db, data.name, exclude=agent_id):
        raise AgentError(f"an agent named '{data.name}' already exists", 409)
    agent.name, agent.description = data.name, data.description
    current = await latest_configuration(db, agent_id)
    new_settings = data.settings.model_dump()
    changed = current is None or (
        AgentSettings.model_validate(current.settings).model_dump() != new_settings
        or current.engine != data.engine
        or current.voice_provider != data.voice_provider
    )
    if changed:
        db.add(
            VoiceAgentConfiguration(
                agent_id=agent_id,
                version=(current.version + 1) if current else 1,
                voice_provider=data.voice_provider,
                engine=data.engine,
                settings=new_settings,
            )
        )
    await db.commit()
    return changed


async def set_default(db: AsyncSession, agent_id: uuid.UUID) -> None:
    agent = await db.get(VoiceAgent, agent_id)
    if agent is None:
        raise AgentError("agent not found", 404)
    if agent.archived:
        raise AgentError("an archived agent cannot be the default")
    for other in (await db.scalars(select(VoiceAgent).where(VoiceAgent.is_default.is_(True)))).all():
        other.is_default = False
    agent.is_default = True
    await db.commit()


async def archive_agent(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Archived, never deleted: past calls keep pointing at their configuration."""
    agent = await db.get(VoiceAgent, agent_id)
    if agent is None:
        raise AgentError("agent not found", 404)
    if agent.is_default:
        raise AgentError("make another agent the default before archiving this one")
    agent.archived = True
    await db.commit()


async def resolve_session(db: AsyncSession, overrides: SessionConfig, settings: Settings) -> tuple[dict[str, Any], VoiceAgentConfiguration]:
    """Config snapshot for a new call/session: the chosen (or default) agent's latest
    version, plus per-call overrides. Records which agent version was used."""
    agent, cfg = await get_usable_agent(db, overrides.agent_id)
    config = resolve_config(overrides, settings, base_from_configuration(cfg))
    config["agent"] = {"id": str(agent.id), "name": agent.name, "version": cfg.version, "configuration_id": str(cfg.id)}
    return config, cfg
