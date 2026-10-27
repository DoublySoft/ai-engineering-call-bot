# Consentimiento, privacidad y grabaciones

Esto es un laboratorio de pruebas, no un servicio de llamadas. Reglas del proyecto:

- **Sólo números autorizados** (`OUTBOUND_ALLOWLIST`), de personas que han aceptado participar en la prueba.
  No hay marcación por lotes, campañas ni llamadas comerciales no solicitadas, y no se deben añadir.
- **Informar de que hablan con una IA.** El saludo por defecto lo dice; mantenlo así. En varias
  jurisdicciones (p. ej. la AI Act de la UE para sistemas que interactúan con personas, y normas de
  telemarketing en EE. UU.) es obligatorio.
- **Grabaciones:** no se graba audio (Twilio `record=False`, y la API no guarda audio). Si en el futuro se
  activa, hará falta consentimiento explícito de todas las partes (en algunos estados de EE. UU. y países
  europeos se exige el de todas), aviso al inicio de la llamada y una política de retención.
- **Transcripciones y números** son datos personales (RGPD): se guardan en tu Postgres local, sólo
  accesibles con `ADMIN_API_TOKEN`. Bórralos cuando no los necesites (`DELETE FROM calls` borra en cascada).
- **Proveedores:** el audio y el texto se envían a Twilio y OpenAI para procesarse. Revisa sus términos y
  políticas de retención de datos antes de usar datos reales.
- Los logs no incluyen claves ni tokens; sí incluyen identificadores de llamada.

Esto no es asesoramiento legal: si el lab se usa con terceros, consulta la normativa aplicable.
