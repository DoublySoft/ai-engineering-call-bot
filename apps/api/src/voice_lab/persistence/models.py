import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from voice_lab.core.database import Base

Json = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(18, 8)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- catalog
class AIProvider(Base):
    __tablename__ = "ai_providers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # "openai", "twilio", ...
    display_name: Mapped[str] = mapped_column(String(100))


class AIModel(Base, Timestamped):
    __tablename__ = "ai_models"
    __table_args__ = (UniqueConstraint("provider_id", "model"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    provider_id: Mapped[str] = mapped_column(ForeignKey("ai_providers.id"))
    model: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(200))
    capabilities: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    context_window: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    deprecated: Mapped[str | None] = mapped_column(String(200))
    pricing: Mapped[list["ModelPricing"]] = relationship(back_populates="model", cascade="all, delete-orphan")


class ModelPricing(Base, Timestamped):
    __tablename__ = "model_pricing"
    __table_args__ = (UniqueConstraint("model_id", "version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_models.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(64))
    effective_from: Mapped[date] = mapped_column(Date)
    components: Mapped[list[dict[str, Any]]] = mapped_column(Json)
    source_url: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    approximate: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(Text)
    model: Mapped[AIModel] = relationship(back_populates="pricing")


# ---------------------------------------------------------------- agents
class VoiceAgent(Base, Timestamped):
    __tablename__ = "voice_agents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # Inbound routing (Phase 5): the Twilio number this agent answers.
    inbound_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    configurations: Mapped[list["VoiceAgentConfiguration"]] = relationship(
        back_populates="agent", order_by="VoiceAgentConfiguration.version"
    )


class VoiceAgentConfiguration(Base, Timestamped):
    """Immutable: editing an agent creates a new version so old calls keep theirs."""

    __tablename__ = "voice_agent_configurations"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_configuration_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("voice_agents.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    voice_provider: Mapped[str] = mapped_column(String(32))  # custom | retell
    engine: Mapped[str] = mapped_column(String(32))  # pipeline | realtime
    settings: Mapped[dict[str, Any]] = mapped_column(Json)  # models, voice, prompt, language
    agent: Mapped[VoiceAgent] = relationship(back_populates="configurations")


# ---------------------------------------------------------------- calls
class Call(Base, Timestamped):
    __tablename__ = "calls"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    direction: Mapped[str] = mapped_column(String(16))  # outbound | inbound | browser
    status: Mapped[str] = mapped_column(String(24), index=True)
    to_number: Mapped[str | None] = mapped_column(String(20))
    from_number: Mapped[str | None] = mapped_column(String(20))
    telephony_provider: Mapped[str | None] = mapped_column(String(32))
    provider_call_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    agent_configuration_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("voice_agent_configurations.id"))
    # Snapshots keep history intact even if config or prices change later.
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(Json)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    stream_token: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    sessions: Mapped[list["ConversationSession"]] = relationship(back_populates="call", order_by="ConversationSession.started_at")


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    engine: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    call: Mapped[Call] = relationship(back_populates="sessions")
    messages: Mapped[list["ConversationMessage"]] = relationship(order_by="ConversationMessage.seq")


class ConversationMessage(Base, Timestamped):
    __tablename__ = "conversation_messages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation_sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # user | agent | system
    text: Mapped[str] = mapped_column(Text)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversation_sessions.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(100))
    operation_type: Mapped[str] = mapped_column(String(24))  # stt | llm | tts | s2s | telephony | managed
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    audio_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    characters: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    usage_source: Mapped[str] = mapped_column(String(24))  # provider_reported | measured | estimated
    cost_usd: Mapped[Decimal | None] = mapped_column(Money)
    cost_status: Mapped[str] = mapped_column(String(24))  # see catalog.pricing.CostStatus
    pricing_version: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallEvent(Base):
    __tablename__ = "call_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversation_sessions.id", ondelete="CASCADE"))
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(100))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
