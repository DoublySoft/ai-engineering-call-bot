# Retell AI (Fase 7)

Retell es la alternativa **gestionada**: hace todo el audio, la detección de turnos, el STT, el LLM y la voz.
El laboratorio no duplica nada de eso. Solo:

1. **Crea el agente en Retell** a partir del agente del laboratorio: un *Retell LLM* (prompt = reglas de voz +
   papel + conocimiento, saludo como `begin_message`, LLM mapeado al catálogo de Retell) y un *agent* (voz,
   idioma, almacenamiento de datos y duración máxima). Se crea **una vez por configuración** y se reutiliza mientras
   no cambie nada que Retell vea (tabla `provider_agent_bindings`).
2. **Da al navegador un token** de llamada web (`POST /v3/create-web-call`, con la clave secreta en el servidor). El
   navegador se conecta con `retell-client-js-sdk` (`RetellWebClient.startCall` con ese token).
3. **Importa el resultado** al terminar (`GET /v2/get-call`): estado, duración, latencias (p50 de Retell),
   coste combinado y transcripción.

## Cómo usarlo

- *New call* → **Browser** → *Voice provider* **Retell AI** (o fíjalo en el agente).
- LLM: uno que Retell soporte (`claude-haiku-5-5` → `claude-5.5-haiku`, `gpt-4.1-mini`, etc.; ver
  `integrations/voice/retell/mapping.py`).
- Voz: un `voice_id` de Retell (por defecto `cartesia-Isabel`, `RETELL_VOICE_ID`).
- También por teléfono, saliente y entrante: el número de Twilio marca a Retell por SIP (`register-phone-call` +
  `<Dial><Sip>`). Ver [telephony.md](telephony.md).

## Coste y privacidad

- Retell factura un importe combinado por llamada. Se registra como **un** `UsageEvent` `managed` /
  `provider_reported`, con el desglose de productos en `details`. No se suman costes de STT/LLM/TTS aparte, así que
  no hay doble conteo.
- `RETELL_DATA_STORAGE=basic_attributes_only` (por defecto) hace que Retell no guarde transcripciones ni
  grabaciones. En ese caso la transcripción viene de los eventos en vivo del navegador y se marca
  `transcript_source: browser`. Con `everything` se usa la de Retell.
- Las latencias de Retell son las suyas (`provider_reported`) y en Analytics no se mezclan con las medidas del
  laboratorio.

## Agentes creados en tu cuenta de Retell

Cada configuración distinta crea un *Retell LLM* y un *agent* llamados `voice-lab · <agente> v<versión>`. Puedes
borrarlos desde el panel de Retell. Si vuelves a usar esa configuración, el laboratorio no los encontrará en su
tabla y creará otros nuevos.
