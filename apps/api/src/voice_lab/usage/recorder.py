import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voice_lab.catalog.pricing import CostResult
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import UsageEvent


class UsageRecorder:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], events: EventRecorder):
        self._sessionmaker = sessionmaker
        self._events = events
        self.total_cost_usd = Decimal(0)

    async def record(
        self,
        *,
        provider: str,
        model: str | None,
        operation_type: str,
        cost: CostResult,
        usage_source: str,
        correlation_id: str | None = None,
        **fields: Any,
    ) -> None:
        details = fields.pop("details", {})
        if cost.missing:
            details = {**details, "missing_pricing_dimensions": list(cost.missing)}
        async with self._sessionmaker() as db:
            db.add(
                UsageEvent(
                    call_id=self._events.call_id,
                    session_id=self._events.session_id,
                    provider=provider,
                    model=model,
                    operation_type=operation_type,
                    usage_source=usage_source,
                    cost_usd=cost.amount_usd,
                    cost_status=cost.status.value,
                    pricing_version=cost.pricing_version,
                    details=details,
                    **fields,
                )
            )
            await db.commit()
        if cost.amount_usd is not None:
            self.total_cost_usd += cost.amount_usd
        await self._events.emit(
            "usage.recorded",
            correlation_id=correlation_id,
            provider=provider,
            model=model,
            operation_type=operation_type,
            usage_source=usage_source,
            cost_usd=str(cost.amount_usd) if cost.amount_usd is not None else None,
            cost_status=cost.status.value,
            total_cost_usd=str(self.total_cost_usd),
            **{k: v for k, v in fields.items() if v is not None},
        )


async def call_cost_total(db: AsyncSession, call_id: uuid.UUID) -> Decimal:
    total = await db.scalar(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(UsageEvent.call_id == call_id))
    return Decimal(total or 0)
