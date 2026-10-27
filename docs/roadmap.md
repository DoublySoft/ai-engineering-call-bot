# Roadmap

| Fase | Estado | Notas |
| --- | --- | --- |
| 1. Foundation | ✅ | monorepo, FastAPI, Next.js, Postgres, Alembic, catálogo, eventos, dashboard |
| 2. Primera llamada outbound | ✅ implementada, ⏳ pendiente de prueba real | ver [first-call.md](first-call.md) |
| 3. Anthropic como LLM | ✅ | Claude Haiku/Sonnet/Opus 5.5 con `effort: low`; probado en vivo |
| 4. Browser voice chat | ✅ probado en vivo, también con micrófono real | ver [browser-voice.md](browser-voice.md) |
| 4b. Agentes configurables | ✅ | instrucciones, conocimiento, saludo, modelos; versionados. Ver [agents.md](agents.md) |
| 5. Inbound | — | webhook `/twilio/voice` → `VoiceAgent.inbound_number` → mismo `<Connect><Stream>` |
| 6. OpenAI Realtime | ✅ probado en vivo | ver [realtime.md](realtime.md) |
| 7. Retell AI | ✅ llamadas web; ⏳ falta probar una llamada con micrófono | ver [retell.md](retell.md); el teléfono vía Retell+Twilio queda fuera (Twilio) |
| 8. Observabilidad avanzada | ✅ | OpenTelemetry + `/analytics`; ver [observability.md](observability.md) |

## Pendiente conocido de Fase 4

- Sin auriculares el modo es half-duplex: el micrófono no envía mientras suena el agente.

## Pendiente conocido de Fase 2

- STT por archivo (sin parciales del usuario). Los parciales del agente sí se muestran.
- `RECORD_AUDIO` no tiene efecto (no se graba).
- Hub de eventos en memoria: un solo proceso de API.
