# Entorno local y puertos

Todo se configura desde el `.env` de la raíz (lo leen Docker Compose, la API y Next.js).

| Variable | Defecto | Uso |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | `voice-ai-lab` | nombre del proyecto Compose (contenedores y volumen) |
| `POSTGRES_HOST_PORT` | `55432` | puerto publicado en `127.0.0.1` |
| `API_PORT` | `8000` | FastAPI |
| `WEB_PORT` | `3000` | Next.js |

`./scripts/check-ports.sh` comprueba si están libres. No asumimos que lo estén.

## Varios proyectos a la vez

- Postgres escucha **siempre en 5432 dentro de su contenedor**; sólo cambia el puerto publicado en el host.
- Para un segundo clon del lab: otro `COMPOSE_PROJECT_NAME` (volumen y red separados) y otros puertos:

```env
COMPOSE_PROJECT_NAME=voice-ai-lab-b
POSTGRES_HOST_PORT=55433
API_PORT=8001
WEB_PORT=3001
DATABASE_URL=postgresql+asyncpg://voice_ai:...@127.0.0.1:55433/voice_ai_lab
API_INTERNAL_URL=http://127.0.0.1:8001
NEXT_PUBLIC_API_WS_URL=ws://127.0.0.1:8001
```

## Host vs. Docker

- API en el host (`pnpm dev:api`): `DATABASE_URL` apunta a `127.0.0.1:${POSTGRES_HOST_PORT}`.
- API en Docker (`docker compose --profile full up`): Compose sobrescribe `DATABASE_URL` a `postgres:5432`
  (red interna) y ejecuta las migraciones al arrancar.

## Base de datos de tests

`pytest` crea y migra `<POSTGRES_DB>_test` en el mismo servidor (la borra y recrea en cada ejecución).
Si Postgres no está levantado, los tests de integración se omiten y los unitarios siguen corriendo.
