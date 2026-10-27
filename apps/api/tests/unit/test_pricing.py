from datetime import date
from decimal import Decimal

from voice_lab.catalog.pricing import CostStatus, calculate_cost
from voice_lab.catalog.registry import get_model
from voice_lab.catalog.schemas import BillingDimension as D
from voice_lab.catalog.schemas import PriceComponent, PriceUnit, PricingVersion


def test_llm_cost_splits_cached_tokens() -> None:
    pricing = get_model("openai", "gpt-4.1-mini").current_pricing()  # type: ignore[union-attr]
    cost = calculate_cost(pricing, {D.INPUT_TEXT_TOKENS: 1_000_000, D.CACHED_INPUT_TEXT_TOKENS: 1_000_000, D.OUTPUT_TEXT_TOKENS: 1_000_000})
    assert cost.amount_usd == Decimal("2.10")  # 0.40 + 0.10 + 1.60
    assert cost.status == CostStatus.CALCULATED


def test_stt_priced_per_minute_from_seconds() -> None:
    pricing = get_model("openai", "gpt-transcribe").current_pricing()  # type: ignore[union-attr]
    cost = calculate_cost(pricing, {D.AUDIO_INPUT_SECONDS: 30})
    assert cost.amount_usd == Decimal("0.00225")


def test_tts_priced_per_character() -> None:
    pricing = get_model("openai", "tts-1").current_pricing()  # type: ignore[union-attr]
    assert calculate_cost(pricing, {D.INPUT_CHARACTERS: 100}).amount_usd == Decimal("0.0015")


def test_missing_dimensions_are_unavailable_not_zero() -> None:
    pricing = get_model("openai", "gpt-4o-mini-tts").current_pricing()  # type: ignore[union-attr]
    cost = calculate_cost(pricing, {D.INPUT_CHARACTERS: 100})
    assert cost.amount_usd is None
    assert cost.status == CostStatus.UNAVAILABLE
    assert "output_audio_tokens" in cost.missing


def test_no_pricing_is_unavailable() -> None:
    assert calculate_cost(None, {D.CALL_SECONDS: 60}).status == CostStatus.UNAVAILABLE


def test_unverified_or_estimated_usage_is_marked_estimated() -> None:
    manual = PricingVersion(
        version="manual-1",
        effective_from=date(2026, 1, 1),
        verified=False,
        components=[PriceComponent(dimension=D.CALL_SECONDS, unit=PriceUnit.USD_PER_MINUTE, price_usd=Decimal("0.014"))],
    )
    assert calculate_cost(manual, {D.CALL_SECONDS: 60}).status == CostStatus.ESTIMATED
    verified = get_model("openai", "tts-1").current_pricing()  # type: ignore[union-attr]
    assert calculate_cost(verified, {D.INPUT_CHARACTERS: 10}, usage_is_estimated=True).status == CostStatus.ESTIMATED


def test_current_pricing_picks_latest_effective_version() -> None:
    model = get_model("openai", "tts-1").model_copy(deep=True)  # type: ignore[union-attr]
    model.pricing.append(
        PricingVersion(
            version="future",
            effective_from=date(2099, 1, 1),
            verified=True,
            components=[PriceComponent(dimension=D.INPUT_CHARACTERS, unit=PriceUnit.USD_PER_1M_CHARACTERS, price_usd=Decimal(1))],
        )
    )
    assert model.current_pricing().version != "future"  # type: ignore[union-attr]
    assert model.current_pricing(date(2099, 6, 1)).version == "future"  # type: ignore[union-attr]


def test_approximate_pricing_is_estimated_even_when_verified() -> None:
    pricing = get_model("openai", "gpt-4o-mini-transcribe").current_pricing()  # type: ignore[union-attr]
    assert pricing.verified and pricing.approximate  # type: ignore[union-attr]
    cost = calculate_cost(pricing, {D.AUDIO_INPUT_SECONDS: 60})
    assert cost.amount_usd == Decimal("0.003") and cost.status == CostStatus.ESTIMATED
