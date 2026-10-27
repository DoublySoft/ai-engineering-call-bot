# OpenAI Realtime (Fase 6)

Motor **speech-to-speech**: un único modelo de OpenAI escucha, detecta el turno, razona y habla. Está separado
del pipeline STT → LLM → TTS (`engines/realtime.py`) e implementa el mismo contrato `ConversationEngine`, así
que funciona con el navegador y con Twilio sin tocar los controladores. **No admite Anthropic**: la sesión es
nativa de OpenAI.

## Cómo usarlo

- En el agente (o solo para una llamada): *Conversation engine* → **OpenAI Realtime**.
- Modelo: `gpt-realtime-2.1-mini` por defecto (`S2S_MODEL`), o `gpt-realtime-2.1`.
- El prompt es el mismo que en el pipeline (reglas de voz + papel + conocimiento del agente).
- Voz: una voz de Realtime (`marin`, `cedar`, `alloy`…). Si la del agente no existe en Realtime se usa `marin`.

## Detalles de implementación

- Conexión WebSocket servidor → OpenAI con el SDK oficial (`client.realtime.connect`). El navegador sigue hablando
  con nuestra API, no con OpenAI.
- Audio `audio/pcm` 24 kHz en ambos sentidos. La entrada se remuestrea desde el transporte (16 kHz navegador,
  8 kHz Twilio).
- Turnos con el `server_vad` de OpenAI (500 ms de silencio) y `interrupt_response`. Si la persona habla encima, se
  borra el audio pendiente en el cliente y se envía `conversation.item.truncate` con lo ya reproducido.
- El saludo se genera con `response.create` indicando la frase exacta.
- Transcripción de la entrada con el modelo STT del agente (`gpt-transcribe`), facturada aparte.

## Coste y métricas

- `response.done` trae el uso real: tokens de texto y audio, de entrada (con la parte cacheada) y de salida. Cada
  dimensión se cobra con su precio (texto/audio × entrada/cacheada/salida) y queda como `s2s` en `usage_events`.
- La transcripción de entrada se registra como `stt` con los segundos que reporta OpenAI.
- Latencia: desde el `speech_stopped` del proveedor hasta el primer delta de audio (medida en el servidor).

Medidas reales (2026-10-27, mismo agente y pregunta, por el navegador):

| Arquitectura | Fin de habla → primer audio | Coste por turno |
| --- | --- | --- |
| OpenAI Realtime (`gpt-realtime-2.1-mini`) | 0,46–1,5 s | ~$0,008 |
| Pipeline (Claude Haiku 5.5 + `tts-1`) | ~3,4 s | ~$0,002 |

Realtime es unas 3–4 veces más rápido y otras tantas más caro por turno. Ver **Analytics** para tus propios datos.
