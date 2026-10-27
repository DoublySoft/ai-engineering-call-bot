# Roadmap

| Fase | Estado | Notas |
| --- | --- | --- |
| 1. Foundation | ✅ | monorepo, FastAPI, Next.js, Postgres, Alembic, catálogo, eventos, dashboard |
| 2. Primera llamada outbound | ✅ implementada, ⏳ pendiente de prueba real | ver [first-call.md](first-call.md) |
| 3. Anthropic como LLM | ✅ | Claude Haiku/Sonnet/Opus 5.5 con `effort: low`; probado en vivo |
| 4. Browser voice chat | ✅ backend probado en vivo; ⏳ falta probar con micrófono real | ver [browser-voice.md](browser-voice.md) |
| 5. Inbound | — | webhook `/twilio/voice` → `VoiceAgent.inbound_number` → mismo `<Connect><Stream>` |
| 6. OpenAI Realtime | — | `RealtimeEngine` (otro `ConversationEngine`, turn detection del proveedor) |
| 7. Retell AI | — | `RetellVoiceProvider`; Twilio vía el mecanismo oficial de Retell, sin duplicar gestión de la llamada |
| 8. Observabilidad avanzada | — | exportar spans a OpenTelemetry, comparativas por modelo/proveedor |

## Pendiente conocido de Fase 4

- Probar con un micrófono real en Chrome (el panel del navegador de pruebas bloquea el micrófono). El servidor
  se ha probado extremo a extremo con un cliente que envía voz real por el WebSocket.
- Sin auriculares el modo es half-duplex: el micrófono no envía mientras suena el agente.

## Pendiente conocido de Fase 2

- STT por archivo (sin parciales del usuario). Los parciales del agente sí se muestran.
- `RECORD_AUDIO` no tiene efecto (no se graba).
- Hub de eventos en memoria: un solo proceso de API.
