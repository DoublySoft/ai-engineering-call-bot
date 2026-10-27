from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.core.database import get_db
from voice_lab.core.security import require_admin
from voice_lab.modules.analytics.service import analytics

router = APIRouter(prefix="/api", tags=["analytics"], dependencies=[Depends(require_admin)])


@router.get("/analytics")
async def get_analytics(days: int = Query(30, ge=1, le=365), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await analytics(db, days)
