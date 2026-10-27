"""OpenTelemetry tracing. Every timed step (stt/llm/tts/s2s) becomes a span carrying
the call, session and turn correlation ids. Spans are exported only when
OTEL_EXPORTER_OTLP_ENDPOINT is set (e.g. a local Jaeger/Tempo/Collector); the
lab's own event log in Postgres does not depend on it."""

import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)
_configured = False


def configure_tracing() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "voice-ai-lab-api")}))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        logger.info("OpenTelemetry export enabled")
    trace.set_tracer_provider(provider)


tracer = trace.get_tracer("voice_lab")
