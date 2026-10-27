# Agentes configurables

Un **agente** define *qué es* la IA en una conversación: su papel, lo que sabe, cómo saluda y qué modelos usa
por defecto. Se gestionan en **Agents** (`/agents`).

## Qué se configura

| Campo | Para qué |
| --- | --- |
| Instrucciones | Quién es el agente y cómo debe actuar (rol, objetivo, reglas). |
| Conocimiento | Datos de referencia: horarios, precios, FAQ… (hasta 30.000 caracteres). Si algo no está ahí, se le pide que no lo invente. |
| Saludo | Lo primero que dice. Vacío = espera a que hable la persona. |
| Idioma | Idioma principal; si la persona habla otro, responde en el suyo. |
| Modelos y voz | STT, LLM, TTS y voz por defecto. "Server default" = lo que diga `.env`. |
| Máx. tokens de respuesta | Límite de longitud de cada respuesta. |

El prompt final que recibe el LLM se compone así (se ve en la ficha del agente):

1. **Reglas de voz** fijas, comunes a todos los agentes (frases cortas, sin markdown ni listas, idioma…).
2. **# Tu papel**: las instrucciones del agente.
3. **# Información de referencia**: el conocimiento, si lo hay.

## Versiones

- Cambiar nombre o descripción no crea versión. Cambiar cualquier cosa que afecte a la conversación crea una
  versión nueva e inmutable.
- Cada llamada guarda en su snapshot el agente, la versión y el prompt exacto usados, así que el historial no
  cambia aunque edites el agente después.
- Los agentes se **archivan**, no se borran. Siempre hay un agente por defecto, que no se puede archivar.

## Usarlo en una llamada o sesión

- En *New call* eliges el agente y los modelos se rellenan con los suyos. Si cambias alguno, el cambio vale solo
  para esa llamada y queda registrado como override.
- Desde la ficha del agente, **Talk to it in the browser** crea una sesión de navegador con él.
- API: `config.agent_id` en `POST /api/calls` y `POST /api/browser-sessions`. Sin `agent_id` se usa el agente
  por defecto.

## Coste del contexto

El conocimiento va en cada petición al LLM. Con Claude se activa la caché de prompt automática: a partir del
segundo turno el prefijo (reglas + papel + conocimiento) se lee a ~10 % del precio de entrada. Los prefijos por
debajo del mínimo del modelo no se cachean. OpenAI cachea automáticamente a partir de 1.024 tokens. En ambos casos
los tokens cacheados se registran y se cobran aparte en `usage_events`.

Ejemplo real (2026-10-27): agente "Recepción Clínica Sonrisa" con Claude Haiku 5.5. A la pregunta
"¿cuánto cuesta una limpieza dental y abrís los sábados?" respondió con los datos del conocimiento (45 €,
sábados de 10:00 a 14:00) y ofreció pedir cita.
