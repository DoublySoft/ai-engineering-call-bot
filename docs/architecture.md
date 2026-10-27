# Arquitectura

## Visión general

```
                 ┌───────────── apps/web (Next.js) ─────────────┐
 navegador ───▶  │ páginas + proxy /api/lab/* (añade ADMIN token) │
     ▲           └───────────────┬───────────────────────────────┘
     │ ws /api/calls/{id}/live   │ HTTP (server-side)
     │ (token HMAC por llamada)  ▼
┌────┴──────────────────── apps/api (FastAPI) ─────────────────────────┐
│ modules/calls      → orquestación, validación, estado de la llamada  │
│ engines/           → ConversationEngine (pipeline hoy; realtime mañana)│
│ integrations/      → adaptadores: telephony/twilio, llm, stt, tts     │
│ observability/     → CallEvent persistido + log JSON + hub en vivo    │
│ usage/ + catalog/  → UsageEvent con coste calculado/estimado/no disp. │
└────┬──────────────────────────────┬─────────────────────────────────┘
     │ REST (calls.create, status)  │ WebSocket Media Stream (μ-law 8 kHz)
     ▼                              ▼
   Twilio  ◀──────── teléfono ────────▶
```

### Flujo de una llamada outbound (Fase 2)

1. `POST /api/calls` valida número (E.164 + `OUTBOUND_ALLOWLIST`), resuelve la configuración contra el
   catálogo y rechaza combinaciones no implementadas.
2. Se crea `Call` con **snapshot** de configuración y de precios, y un `stream_token` de un solo uso.
3. Twilio `calls.create` con TwiML inline `<Connect><Stream>` (bidireccional), `status_callback` y `time_limit`.
4. Al descolgar, Twilio abre `wss://PUBLIC_BASE_URL/twilio/media`; el mensaje `start` trae `call_id` + token
   como `customParameters`. Se valida el token, se crea `ConversationSession` y el engine.
5. Audio entrante μ-law → PCM16 → VAD por energía → al detectar fin de turno: STT → LLM (streaming) →
   TTS por frases → PCM 24 kHz → decimación a 8 kHz → μ-law → `media` a Twilio, y un `mark` al final.
6. Si el usuario habla mientras el agente suena → `clear` a Twilio + cancelar el turno (barge-in).
7. `status_callback` (firma validada) actualiza estado/duración; al completar se consulta el `price`
   del recurso Call de Twilio y se registra como coste de telefonía `provider_reported`.

## Contratos principales

```python
class AudioTransport(Protocol):           # engines/base.py
    name: str; input_sample_rate: int
    async def send_audio(self, samples, sample_rate) -> None
    async def flush_marker(self, label) -> None   # avisa cuando lo encolado se ha reproducido
    async def clear_audio(self) -> None           # barge-in
    async def hangup(self, reason) -> None

class ConversationEngine(Protocol):
    capabilities: EngineCapabilities      # streaming_stt, barge_in, turn_detection (local_vad|provider)...
    async def start(self) -> None
    async def handle_audio(self, samples) -> None  # no bloquea: los turnos corren en tareas
    async def on_playback_done(self, label) -> None
    async def end(self, reason) -> dict            # métricas de sesión
```

El controlador de Twilio sólo conoce `AudioTransport` + `ConversationEngine` (vía `engines/factory.py`).
Un motor Realtime (Fase 6) o el navegador (Fase 4, otro `AudioTransport`) no tocan el controlador.

Adaptadores de proveedor: `LLMClient.stream(model, messages, max_output_tokens) -> AsyncIterator[LLMChunk]`
(el último chunk lleva `LLMUsage` con tokens de entrada **sin** cachear, cacheados y de salida),
`BatchSTT.transcribe(...) -> Transcript` y `TTS.synthesize(...) -> AsyncIterator[PCM16]`.
Las capacidades se declaran (`STTCapabilities.streaming_partials`, `EngineCapabilities`) en lugar de
asumir que todos los proveedores hacen lo mismo.

## Catálogo y precios

`catalog/schemas.py` separa: **definición** (`ModelDefinition`), **capacidades** (`Capabilities`),
**dimensiones de facturación** (`BillingDimension`: tokens de texto/audio, segundos, caracteres…),
**unidades explícitas** (`PriceUnit`: USD por 1M tokens / por minuto / por 1M caracteres) y
**versiones de precio** fechadas y con fuente (`PricingVersion.source_url`, `verified`).
El consumo real vive en `usage_events`, nunca en el catálogo.

- Precios copiados de la página oficial de OpenAI el 2026-10-27. Twilio no tiene precio en catálogo:
  usamos el que Twilio devuelve por llamada.
- Precios manuales: `PRICING_OVERRIDES_FILE=/ruta.json` con `[{"key":"openai:gpt-4.1-mini", "version":"manual-1", "effective_from":"2026-11-01", "components":[...]}]`.
  Se marcan `verified=false` y su coste queda como `estimated`.
- `catalog/sync.py` refleja el catálogo en `ai_models` / `model_pricing` al arrancar (append-only),
  para poder cruzar por SQL.

`cost_status` de cada `UsageEvent`:

| Valor | Significado |
| --- | --- |
| `provider_reported` | importe dado por el proveedor (Twilio `price`) |
| `calculated` | consumo medido o reportado × precio verificado |
| `estimated` | falta alguna dimensión, precio no verificado o consumo estimado |
| `unavailable` | no hay precio o no se puede medir la dimensión facturada (p. ej. tokens de audio de `gpt-4o-mini-tts`) |

`usage_source` distingue `provider_reported` (tokens del LLM), `measured` (segundos de audio enviados a STT,
caracteres enviados a TTS) y `estimated`. El panel muestra "Known cost" y avisa de partidas sin precio:
nunca se presenta una estimación como consumo confirmado.

## Observabilidad

Cada evento (`call.created`, `call.ringing`, `call.answered`, `session.started`, `speech.started/ended`,
`stt.*`, `llm.*`, `tts.*`, `audio.playback.started/completed`, `agent.interrupted`, `usage.recorded`,
`session.completed`, `call.completed/failed`, `*.failed`, `call.*_limit_reached`) se:

1. persiste en `call_events` (call_id, session_id, `correlation_id` del turno, proveedor, modelo, duración, datos);
2. escribe como log JSON (con redacción de claves sensibles);
3. publica en el hub en memoria → WebSocket `/api/calls/{id}/live`.

Métricas por sesión (`conversation_sessions.metrics`): STT, LLM time-to-first-token y total, TTS first byte,
**fin de habla → primer audio enviado**. Todas son **medidas en el servidor**; el tiempo de red hasta el
teléfono y el buffer de Twilio no se ven y por eso se indica `measurement_note`.

OpenTelemetry: no se ha añadido todavía. Los `span()` de `EventRecorder` tienen ya forma de span
(started/completed/failed + duración + correlation_id), así que exportarlos a OTel es un cambio local
en `observability/events.py` (Fase 8) sin tocar los motores.

## Persistencia

`AIProvider`, `AIModel`, `ModelPricing`, `VoiceAgent`, `VoiceAgentConfiguration` (versionada e inmutable),
`Call` (con `config_snapshot` y `pricing_snapshot`), `ConversationSession`, `ConversationMessage`,
`UsageEvent`, `CallEvent`. Migraciones con Alembic (`apps/api/migrations`); no se usa `create_all()`.

## Seguridad

- Todos los endpoints `/api/*` exigen `Authorization: Bearer ADMIN_API_TOKEN` (incluidas transcripciones).
  El token sólo lo conoce el servidor de Next; el navegador habla con el proxy `/api/lab/*` (con lista
  blanca de rutas). El WebSocket en vivo usa un token HMAC por llamada derivado del admin token.
- Webhooks de Twilio: firma `X-Twilio-Signature` validada contra `PUBLIC_BASE_URL` + ruta.
- Media Stream: token aleatorio de un solo uso por llamada, enviado en el `<Parameter>` del TwiML.
- Outbound sólo a `OUTBOUND_ALLOWLIST`, números E.164. Sin marcación por lotes ni campañas.
- Límite de duración: `time_limit` de Twilio + watchdog propio. Límite de gasto por llamada
  (`MAX_CALL_COST_USD`): cuelga al superarlo (sólo cuenta costes conocidos).
- Errores internos → `500 internal error` sin detalles del proveedor; detalle en logs.
- No se graba audio (`record=False`; `RECORD_AUDIO` está reservado y no tiene efecto todavía).

## Decisiones y desviaciones respecto a la estructura orientativa

- `catalog/` en lugar de `modules/models/`: el catálogo es dato tipado, no un módulo HTTP.
- `engines/` como paquete de primer nivel: es el punto de extensión principal y no es una integración.
- `audio/` aparte: códec μ-law y resampling con numpy (`audioop` desapareció en Python 3.13) y VAD.
- Sin `modules/providers` ni repositorios genéricos: los adaptadores viven en `integrations/` y el registro
  (`integrations/registry.py`) declara lo implementado; la UI lo usa para deshabilitar combinaciones.
- Hub de eventos en memoria: la API corre como un único proceso. Con varios workers habría que pasar a
  Postgres LISTEN/NOTIFY (sin servicios nuevos).

## Riesgos técnicos identificados

| Riesgo | Mitigación actual | Siguiente paso |
| --- | --- | --- |
| Latencia del pipeline batch (VAD 700 ms + STT de archivo + LLM + TTS) | LLM en streaming, TTS por frases, modelos no-reasoning por defecto | STT en streaming (`gpt-live-transcribe` o similar) con parciales; Realtime (Fase 6) |
| VAD por energía con ruido de línea | umbral adaptativo, 100 ms para empezar habla | VAD del proveedor o modelo (silero) |
| Eco/barge-in falso | Twilio sólo envía la pista del llamante | ajustar umbral durante reproducción |
| `tts-1`, `gpt-4o-mini-tts` y los `*-transcribe` 4o están deprecados (ene/feb 2027) | marcados en el catálogo; `gpt-transcribe` por defecto | migrar TTS a Realtime o a otro proveedor |
| Coste de `gpt-4o-mini-tts` no medible (tokens de audio no devueltos) | `cost_status=unavailable` explícito | — |
| Precio de Twilio llega con retraso | sondeo 6×20 s y, si no llega, `unavailable` | job de reconciliación |
| Hub en memoria (un solo proceso) | documentado | LISTEN/NOTIFY |
