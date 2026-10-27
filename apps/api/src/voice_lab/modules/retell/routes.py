import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.core.config import Settings, get_settings
from voice_lab.core.database import get_db
from voice_lab.core.errors import LabError
from voice_lab.core.security import require_admin
from voice_lab.integrations.voice.retell.client import RetellClient
from voice_lab.modules.retell import service

router = APIRouter(prefix="/api/calls", tags=["retell"], dependencies=[Depends(require_admin)])


def get_retell(settings: Settings = Depends(get_settings)) -> RetellClient:
    if not settings.retell_api_key:
        raise HTTPException(503, "RETELL_API_KEY is not set")
    return RetellClient(settings.retell_api_key)


class Utterance(BaseModel):
    role: Literal["agent", "user"]
    content: str = Field(max_length=5000)


class SyncRequest(BaseModel):
    transcript: list[Utterance] = Field(default_factory=list, max_length=500)


@router.post("/{call_id}/retell-web-call")
async def retell_web_call(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    retell: RetellClient = Depends(get_retell),
) -> dict:
    try:
        web = await service.start_web_call(db, call_id, retell, settings)
    except LabError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    # Finalised server-side even if the browser never reports back.
    service.ensure_watcher(call_id, retell, settings.max_call_duration_seconds)
    return web


@router.post("/{call_id}/retell-sync")
async def retell_sync(
    call_id: uuid.UUID,
    body: SyncRequest | None = None,
    retell: RetellClient = Depends(get_retell),
    settings: Settings = Depends(get_settings),
) -> dict:
    transcript = [u.model_dump() for u in body.transcript] if body else None
    service.remember_browser_transcript(call_id, transcript)
    try:
        result = await service.sync_call(call_id, retell, transcript)
    except LabError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if not (result["call_status"] in service.ENDED and result["cost_available"]):
        service.ensure_watcher(call_id, retell, settings.max_call_duration_seconds)
        result["background_sync"] = True
    return result
