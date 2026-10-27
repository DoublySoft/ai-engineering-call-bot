from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.core.config import Settings, get_settings
from voice_lab.core.database import get_db
from voice_lab.core.errors import LabError
from voice_lab.core.security import require_admin
from voice_lab.modules.telephony import service

router = APIRouter(prefix="/api/telephony", tags=["telephony"], dependencies=[Depends(require_admin)])


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return await service.telephony_status(db, settings)


@router.post("/configure")
async def configure(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    try:
        return await service.configure_number(settings)
    except LabError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
