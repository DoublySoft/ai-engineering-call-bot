# Roadmap

| Fase | Estado | Notas |
| --- | --- | --- |
| 1. Foundation | ✅ | monorepo, FastAPI, Next.js, Postgres, Alembic, catálogo, eventos, dashboard |
| 2. Primera llamada outbound | ✅ implementada; ⏳ solo falta comprar y conectar el número | ver [telephony.md](telephony.md) |
| 3. Anthropic como LLM | ✅ | Claude Haiku/Sonnet/Opus 5.5 con `effort: low`; probado en vivo |
| 4. Browser voice chat | ✅ probado en vivo, también con micrófono real | ver [browser-voice.md](browser-voice.md) |
| 4b. Agentes configurables | ✅ | instrucciones, conocimiento, saludo, modelos; versionados. Ver [agents.md](agents.md) |
| 5. Inbound | ✅ implementada; ⏳ solo falta el número | agente por número, por defecto o Retell; ver [telephony.md](telephony.md) |
| 6. OpenAI Realtime | ✅ probado en vivo | ver [realtime.md](realtime.md) |
| 7. Retell AI | ✅ web y teléfono (SIP); ⏳ falta probar con micrófono y con número | ver [retell.md](retell.md) |
| 8. Observabilidad avanzada | ✅ | OpenTelemetry + `/analytics`; ver [observability.md](observability.md) |

## Pendiente conocido de Fase 4

- Sin auriculares el modo es half-duplex: el micrófono no envía mientras suena el agente.

## Pendiente conocido de Fase 2

- STT por archivo (sin parciales del usuario). Los parciales del agente sí se muestran.
- `RECORD_AUDIO` no tiene efecto (no se graba).
- Hub de eventos en memoria: un solo proceso de API.
