# Telefonía (Twilio): de "sin número" a llamadas reales

Todo lo de teléfono está implementado y probado con Twilio simulado. **Solo falta comprar el número y conectarlo.**
Mientras no lo hagas, el laboratorio funciona igual: navegador, agentes, Realtime, Retell web y Analytics.

| Flujo | Motores de este lab (pipeline / Realtime) | Retell |
| --- | --- | --- |
| **Saliente** (la web te llama) | Twilio `<Connect><Stream>` → `/twilio/media` | Retell `register-phone-call` → Twilio `<Dial><Sip>` |
| **Entrante** (alguien llama al número) | webhook `/twilio/voice` → agente del número → `<Stream>` | mismo webhook → `<Dial><Sip>` |

## 1. Comprar el número

- En Twilio: *Phone Numbers → Buy a number*, con capacidad **Voice**.
- Cuenta *trial*: solo puede llamar a números verificados (*Verified Caller IDs*) y reproduce un aviso al empezar.
- Activa los permisos geográficos de voz para los países a los que vayas a llamar (*Voice → Settings → Geo
  permissions*).

## 2. `.env`

```env
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+34...        # el número comprado, E.164
OUTBOUND_ALLOWLIST=+34600111222   # a quién puede llamar la web (tu móvil)
INBOUND_ALLOWLIST=                # vacío = cualquiera puede llamar; o lista de números permitidos
PUBLIC_BASE_URL=https://<tu-tunel>
```

## 3. Túnel HTTPS hacia la API

Twilio tiene que alcanzar los webhooks y el WebSocket de audio:

```bash
ngrok http 8000
```

Pon la URL https en `PUBLIC_BASE_URL` y reinicia la API (`pnpm dev:api`). Si el dominio del túnel cambia, repite este
paso y el 4.

## 4. Conectar el número

Abre **Telephony** (`/telephony`):

1. **Re-check**: comprueba variables, credenciales, que el número es tuyo y tiene voz, que el túnel llega a *esta*
   API, la lista de salientes y las claves de IA.
2. **Configure number**: apunta el webhook de llamadas entrantes (`/twilio/voice`) y el de estado (`/twilio/status`)
   del número a este laboratorio (vía API de Twilio). Las salientes no lo necesitan; las entrantes sí.

Cuando *Outbound* e *Inbound* aparecen como **ready**, ya está.

## 5. Probar

- **Saliente:** *New call* → *Outbound phone call* → número de la lista → agente y arquitectura → *Start call*.
- **Entrante:** llama al número. Contesta el agente que tenga ese número en *Agents → Answers inbound calls to*, o el
  agente por defecto.
- Funciona con las tres arquitecturas: pipeline, OpenAI Realtime y Retell.

## Qué se registra

- Estado y duración: por los webhooks de estado de Twilio (firmados).
- Coste de telefonía: el `price` que publica Twilio en la llamada (`provider_reported`), aparte del coste de IA.
- Con Retell, además, su coste combinado (`managed`). No se solapa con el de Twilio: Retell no cobra la telefonía en
  este modo.

## Límites y seguridad

- Webhooks validados con `X-Twilio-Signature` contra `PUBLIC_BASE_URL`.
- Audio por WebSocket autenticado con un token de un solo uso por llamada.
- Límite de duración: `time_limit` en salientes, `timeLimit` en el `<Dial>` de Retell y un watchdog en el servidor.
  Límite de gasto por llamada con los motores propios.
- Si algo falla al contestar una entrante (clave de IA ausente, error del proveedor), quien llama oye un mensaje de
  disculpa y se cuelga; nunca silencio.
- Twilio puede reintentar el webhook de entrada: se reutiliza la misma llamada, sin crear duplicados ni registrar dos
  veces en Retell.

## Retell por teléfono: método usado

Retell recomienda *Elastic SIP trunking*, que exige crear un trunk SIP en Twilio e importar el número en Retell. Aquí
se usa su otro método oficial, *Dial to SIP URI* (`register-phone-call` + `sip:{call_id}@sip.retellai.com`), porque
no necesita configuración extra en Twilio. Limitación: las transferencias de Retell no están disponibles.

## Problemas típicos

| Síntoma | Causa probable |
| --- | --- |
| *Configure number* desactivado | Falta alguna de las cuatro primeras comprobaciones |
| "tunnel … not reachable" | ngrok parado o apuntando a otro puerto |
| Entrante: suena y cuelga | Webhook del número no configurado o firma inválida (`PUBLIC_BASE_URL` distinta) |
| Entrante: mensaje de disculpa | Falta la clave del proveedor del agente; mira los logs de la API |
| Saliente: 403 | El número no está en `OUTBOUND_ALLOWLIST` |
| Saliente: 502 | Twilio rechazó la llamada (trial sin verificar, permisos geográficos); el error se ve en el detalle |
