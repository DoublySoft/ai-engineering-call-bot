# Observabilidad (Fase 8)

## Qué se registra

- **Eventos** (`call_events`): todo el ciclo de la llamada, con `correlation_id` por turno. Se ven en el timeline de
  cada llamada y se emiten como logs JSON.
- **Consumo y coste** (`usage_events`): por operación (`stt`, `llm`, `tts`, `s2s`, `managed`, `telephony`), con
  origen del dato (`usage_source`) y estado del coste (`cost_status`).
- **Métricas de sesión** (`conversation_sessions.metrics`): latencias medias por etapa.
- **Spans de OpenTelemetry**: `voice.stt`, `voice.llm`, `voice.tts` y `voice.s2s`, con `voice.call_id`,
  `voice.session_id`, `voice.correlation_id`, `gen_ai.system` y `gen_ai.request.model`. Los errores quedan marcados
  en el span.

## Exportar trazas

Desactivado por defecto. Para enviarlas a un collector OTLP (Jaeger, Tempo, Grafana, Honeycomb…):

```env
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
OTEL_SERVICE_NAME=voice-ai-lab-api
```

Ejemplo local con Jaeger (UI en http://127.0.0.1:16686):

```bash
docker run --rm -p 127.0.0.1:16686:16686 -p 127.0.0.1:4318:4318 jaegertracing/all-in-one:1.62.0
```

## Analytics (`/analytics`)

Compara arquitecturas y modelos en una ventana de 1, 7, 30 o 90 días:

- **Fin de habla → primer audio** p50/p90 por arquitectura y modelo, indicando si es *medido* por el laboratorio o
  *reportado* por Retell.
- **Coste por turno y por minuto** (solo coste conocido, avisando si hay partidas sin precio).
- Latencia media por etapa (STT, primer token del LLM y primer byte del TTS), errores, sesiones y duración.
- Coste por proveedor y operación.

API: `GET /api/analytics?days=30`.

Límites: son medidas del servidor (no incluyen red ni reproducción en el cliente). El pipeline cierra el turno tras
700 ms de silencio y Realtime tras 500 ms.
