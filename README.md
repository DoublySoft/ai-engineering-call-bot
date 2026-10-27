# Voice AI Lab

Laboratorio para experimentar con agentes de voz: llamadas telefónicas reales (Twilio), distintas
arquitecturas (pipeline STT → LLM → TTS, speech-to-speech, proveedores gestionados) y medición de
latencia, consumo y coste por componente.

> Estado: **Fases 1–4**: foundation, llamada outbound (Twilio), Anthropic como LLM y chat de voz en el
> navegador. Ver [docs/roadmap.md](docs/roadmap.md).

## Stack

| Pieza | Tecnología |
| --- | --- |
| Monorepo | pnpm workspaces (JS) + uv (Python, independiente) |
| API | FastAPI, SQLAlchemy 2 async, Alembic, asyncpg |
| Web | Next.js 15 (App Router), Tailwind 4 |
| BD | PostgreSQL 17 vía Docker Compose |
| Voz | Twilio Media Streams o micrófono del navegador · STT/TTS OpenAI · LLM OpenAI o Claude |

```
apps/api        FastAPI (uv)          → docs/architecture.md
apps/web        Next.js dashboard
packages/shared contratos TS (espejo de los modelos pydantic)
packages/config tsconfig base
infrastructure/docker  Dockerfile de la API
scripts/check-ports.sh comprueba puertos libres
```

## Arranque rápido

```bash
cp .env.example .env          # rellena POSTGRES_PASSWORD, ADMIN_API_TOKEN y las claves
./scripts/check-ports.sh      # cambia puertos en .env si alguno está ocupado
pnpm install
(cd apps/api && uv sync)
pnpm db:up && pnpm db:migrate
pnpm dev:api                  # http://127.0.0.1:8000/health
pnpm dev:web                  # http://localhost:3000
```

Para hablar con la IA desde el navegador (sin Twilio) sigue [docs/browser-voice.md](docs/browser-voice.md).
Para una llamada telefónica real (Twilio + túnel público), [docs/first-call.md](docs/first-call.md).

## Tests

```bash
pnpm test:api                         # unit + integration (necesita Postgres levantado; si no, se omiten)
cd apps/api && uv run pytest -m manual tests/manual -s   # golpea OpenAI y Anthropic de verdad (céntimos)
pnpm --filter web typecheck && pnpm --filter web lint
```

Los tests automáticos nunca llaman a Twilio ni a OpenAI: usan mocks y proveedores falsos.

## Documentación

- [Arquitectura, contratos y decisiones](docs/architecture.md)
- [Entorno local, puertos y varios proyectos](docs/local-setup.md)
- [Chat de voz en el navegador](docs/browser-voice.md)
- [Primera llamada real](docs/first-call.md)
- [Consentimiento, privacidad y grabaciones](docs/compliance.md)
- [Roadmap por fases](docs/roadmap.md)
