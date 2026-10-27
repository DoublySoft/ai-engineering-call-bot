from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from voice_lab.catalog.schemas import BillingDimension, PricingVersion


class CostStatus(StrEnum):
    PROVIDER_REPORTED = "provider_reported"  # the provider told us the amount
    CALCULATED = "calculated"  # measured/reported usage x verified price
    ESTIMATED = "estimated"  # usage or price is an estimate
    UNAVAILABLE = "unavailable"  # missing price or missing usage dimension


@dataclass(frozen=True)
class CostResult:
    amount_usd: Decimal | None
    status: CostStatus
    pricing_version: str | None
    missing: tuple[str, ...] = ()


def calculate_cost(
    pricing: PricingVersion | None,
    quantities: dict[BillingDimension, int | float | Decimal],
    usage_is_estimated: bool = False,
) -> CostResult:
    if pricing is None:
        return CostResult(None, CostStatus.UNAVAILABLE, None, ("pricing",))

    total = Decimal(0)
    missing: list[str] = []
    for component in pricing.components:
        qty = quantities.get(component.dimension)
        if qty is None:
            missing.append(component.dimension.value)
            continue
        total += Decimal(str(qty)) / component.unit.divisor * component.price_usd

    if missing and total == 0:
        return CostResult(None, CostStatus.UNAVAILABLE, pricing.version, tuple(missing))
    if missing or usage_is_estimated or not pricing.verified or pricing.approximate:
        status = CostStatus.ESTIMATED
    else:
        status = CostStatus.CALCULATED
    return CostResult(total.quantize(Decimal("0.00000001")), status, pricing.version, tuple(missing))
