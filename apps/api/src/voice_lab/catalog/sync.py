"""Mirrors the in-code catalog into ai_providers / ai_models / model_pricing.

The code is the source of truth (reviewed in PRs, with sources); the tables
exist so SQL analysis can join usage with what a model cost at the time.
Pricing versions are append-only: an existing version is never rewritten.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voice_lab.catalog.registry import all_models
from voice_lab.persistence.models import AIModel, AIProvider, ModelPricing


async def sync_catalog(db: AsyncSession) -> None:
    for definition in all_models():
        provider = await db.get(AIProvider, definition.provider.value)
        if provider is None:
            db.add(AIProvider(id=definition.provider.value, display_name=definition.provider.value.title()))
            await db.flush()
        row = await db.scalar(select(AIModel).where(AIModel.provider_id == definition.provider.value, AIModel.model == definition.model))
        if row is None:
            row = AIModel(provider_id=definition.provider.value, model=definition.model)
            db.add(row)
        row.kind = definition.kind.value
        row.display_name = definition.display_name
        row.capabilities = definition.capabilities.model_dump()
        row.context_window = definition.context_window
        row.max_output_tokens = definition.max_output_tokens
        row.deprecated = definition.deprecated
        await db.flush()
        known = set((await db.scalars(select(ModelPricing.version).where(ModelPricing.model_id == row.id))).all())
        for pricing in definition.pricing:
            if pricing.version not in known:
                db.add(
                    ModelPricing(
                        model_id=row.id,
                        version=pricing.version,
                        effective_from=pricing.effective_from,
                        components=[c.model_dump(mode="json") for c in pricing.components],
                        source_url=pricing.source_url,
                        verified=pricing.verified,
                        notes=pricing.notes,
                    )
                )
    await db.commit()
