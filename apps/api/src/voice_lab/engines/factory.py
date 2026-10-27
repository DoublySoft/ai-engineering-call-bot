from voice_lab.engines.base import ConversationEngine, SessionContext
from voice_lab.engines.pipeline import PipelineEngine
from voice_lab.engines.realtime import RealtimeEngine
from voice_lab.integrations import registry


def create_engine(ctx: SessionContext) -> ConversationEngine:
    engine = ctx.config["engine"]
    if engine == "pipeline":
        return PipelineEngine(
            ctx,
            stt=registry.stt_client(ctx.config["stt_provider"]),
            llm=registry.llm_client(ctx.config["llm_provider"]),
            tts=registry.tts_client(ctx.config["tts_provider"]),
        )
    if engine == "realtime":
        return RealtimeEngine(ctx, client=registry.openai_client())
    raise registry.ProviderNotAvailable(f"engine '{engine}' is not implemented yet")
