# Primera llamada real (Fase 2)

Criterio de aceptación: desde el frontend introduzco un número autorizado, pulso **Start call**, recibo la
llamada, hablo con la IA, la conversación aparece en pantalla y queda en PostgreSQL.

## 1. Requisitos

- Cuenta de Twilio con un número con capacidad de voz. En cuenta *trial*, el número de destino debe estar
  verificado en Twilio (Verified Caller IDs) y se oye un aviso de trial antes de conectar.
- Permisos geográficos de voz de Twilio habilitados para el país del número destino.
- `OPENAI_API_KEY` con acceso a `gpt-transcribe`, `gpt-4.1-mini` y `tts-1`.
- Un túnel HTTPS público hacia la API (Twilio debe alcanzar el webhook y el WebSocket), por ejemplo:

```bash
ngrok http 8000
```

## 2. `.env`

```env
PUBLIC_BASE_URL=https://<tu-subdominio>.ngrok-free.app   # sin barra final
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...        # número de Twilio (E.164)
OUTBOUND_ALLOWLIST=+34600111222  # tu móvil, E.164; varios separados por comas
OPENAI_API_KEY=sk-...
ADMIN_API_TOKEN=<aleatorio largo>
MAX_CALL_DURATION_SECONDS=300
MAX_CALL_COST_USD=0.50
```

`PUBLIC_BASE_URL` debe coincidir exactamente con la URL que usa Twilio: la firma de los webhooks se valida
contra ella. Si reinicias ngrok y cambia el dominio, actualízalo y reinicia la API.

No hace falta configurar nada en la consola de Twilio para outbound: el TwiML y el `status_callback` van
en la propia petición `calls.create`.

## 3. Arrancar

```bash
pnpm db:up && pnpm db:migrate
pnpm dev:api
pnpm dev:web
```

Comprueba `http://localhost:3000/calls/new`: si falta algo (Twilio, OpenAI, URL pública) aparece un aviso.

Prueba opcional de OpenAI sin llamar por teléfono (TTS → STT y LLM reales, coste ínfimo):

```bash
cd apps/api && uv run pytest -m manual tests/manual -s
```

## 4. Llamar

1. *New call* → elige tu número → modelos (por defecto `gpt-transcribe` / `gpt-4.1-mini` / `tts-1`).
2. *Start call* → se abre la vista en vivo. Al descolgar oirás el saludo.
3. Habla; tras ~0,7 s de silencio el agente responde. Puedes interrumpirle hablando encima.
4. Cuelga (o *Hang up* en la UI). Al terminar verás métricas de latencia y, en un minuto aprox., el coste de
   Twilio (`provider_reported`).

## 5. Si algo falla

| Síntoma | Causa probable |
| --- | --- |
| 403 "not in OUTBOUND_ALLOWLIST" | número no autorizado o no en E.164 |
| 502 "Twilio rejected the call" | ver `call.error` en el detalle (número no verificado en trial, permisos geográficos…) |
| Suena y se corta al descolgar | Twilio no alcanza `wss://PUBLIC_BASE_URL/twilio/media` (túnel caído o URL distinta) |
| Estado nunca pasa de `initiated` | `status_callback` rechazado (403 firma) → `PUBLIC_BASE_URL` no coincide |
| Silencio tras hablar | ver `stt.failed` / `turn.failed` en el timeline (clave de OpenAI, modelo sin acceso) |

Logs: la API escribe JSON por stdout (sin secretos).

## Qué está verificado y qué no

- Verificado con tests automáticos (mocks): flujo REST → Twilio, TwiML, firma de webhooks, estados,
  WebSocket de Media Stream completo con proveedores falsos, persistencia de transcripción, eventos y costes,
  barge-in, límite de gasto, resampling y códec μ-law.
- **No verificado todavía**: una llamada real extremo a extremo con Twilio y OpenAI. Requiere tus
  credenciales, un número verificado y un túnel. Es el paso de validación de esta fase.
