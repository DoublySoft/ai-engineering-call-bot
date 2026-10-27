# Chat de voz en el navegador (Fase 4)

Habla con el agente desde el micrófono, sin Twilio ni túnel. Usa los mismos motores, eventos, costes y
persistencia que una llamada telefónica (no hay coste de telefonía).

## Requisitos

- `OPENAI_API_KEY` (STT y TTS) y, si eliges Claude, `ANTHROPIC_API_KEY`.
- Chrome o Edge recientes. La página debe servirse desde `localhost` (o HTTPS) para que el navegador permita
  el micrófono.
- Auriculares recomendados (ver "Interrupciones").

## Pasos

```bash
pnpm db:up && pnpm db:migrate
pnpm dev:api
pnpm dev:web
```

1. Abre `http://localhost:3000/calls/new` → **Browser (microphone)**.
2. Elige el LLM (OpenAI o Anthropic) y el resto de modelos → **Create browser session**.
3. En la vista de la sesión pulsa **Start talking** y acepta el permiso de micrófono.
4. Oirás el saludo. Habla; tras ~0,7 s de silencio el agente responde. La barra verde muestra el nivel del micrófono.
5. **End session** termina y guarda las métricas. Cada sesión admite una sola conexión: para repetir, crea otra.

## Interrupciones

Sin auriculares, el micrófono capta la voz del agente. Por eso, por defecto, la página **no envía audio
mientras el agente habla** (half-duplex). Con auriculares, marca *Allow interrupting the agent* antes de empezar:
el micrófono envía siempre y puedes cortar al agente hablando encima (barge-in).

## Protocolo (por si quieres otro cliente)

- `POST /api/browser-sessions` (admin) → `{id}`; `POST /api/calls/{id}/browser-token` (admin) → `{path, …}`.
- `ws://API{path}`: envía tramas binarias PCM16 LE mono 16 kHz; recibe PCM16 24 kHz y JSON
  `{"type":"mark"|"clear"|"hangup"}`. Devuelve `{"type":"mark","name":…}` cuando ese audio ha sonado y
  `{"type":"stop"}` para terminar.

## Qué está probado

- Tests automáticos: sesión completa por WebSocket con proveedores falsos (audio binario, marks, cierre,
  persistencia, sin coste de telefonía, token de un solo uso).
- En vivo (2026-10-27): un cliente Python que envía voz real por el WebSocket contra la API con
  `gpt-transcribe` + Claude Haiku/Sonnet 5.5 o GPT-4.1 mini + `tts-1`: saludo, transcripción, respuesta
  correcta, métricas y costes.
- **Pendiente:** probar con micrófono real en Chrome. El navegador integrado que usé para revisar la UI
  bloquea el micrófono; la página muestra el error correctamente.
