from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.core.database import get_db
from voice_lab.core.security import require_admin
from voice_lab.persistence.models import Call, ConversationSession, UsageEvent

router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[Depends(require_admin)])


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    by_direction = dict((await db.execute(select(Call.direction, func.count()).group_by(Call.direction))).all())
    duration = await db.scalar(select(func.coalesce(func.sum(Call.duration_seconds), 0)))
    cost_rows = (
        await db.execute(
            select(UsageEvent.operation_type, UsageEvent.cost_status, func.sum(UsageEvent.cost_usd), func.count()).group_by(
                UsageEvent.operation_type, UsageEvent.cost_status
            )
        )
    ).all()
    tokens = (
        await db.execute(select(func.coalesce(func.sum(UsageEvent.input_tokens), 0), func.coalesce(func.sum(UsageEvent.output_tokens), 0)))
    ).one()
    active = await db.scalar(select(func.count()).select_from(ConversationSession).where(ConversationSession.status == "active"))
    total = sum((Decimal(r[2] or 0) for r in cost_rows), Decimal(0))
    return {
        "calls": {
            "outbound": by_direction.get("outbound", 0),
            "inbound": by_direction.get("inbound", 0),
            "browser": by_direction.get("browser", 0),
        },
        "total_duration_seconds": int(duration or 0),
        "tokens": {"input": int(tokens[0]), "output": int(tokens[1])},
        "cost_total_usd": str(total),
        "cost_breakdown": [
            {"operation": r[0], "cost_status": r[1], "cost_usd": str(r[2]) if r[2] is not None else None, "events": r[3]} for r in cost_rows
        ],
        "active_sessions": int(active or 0),
    }
