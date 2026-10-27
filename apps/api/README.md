# voice-lab API

FastAPI + SQLAlchemy async. Gestionado con [uv](https://docs.astral.sh/uv/), independiente de pnpm.

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn voice_lab.main:app --reload
uv run pytest            # unit + integration
uv run ruff check src tests && uv run ruff format --check src tests
```

Nueva migración tras cambiar `persistence/models.py`:

```bash
uv run alembic revision --autogenerate -m "describe change"
```

Ver [../../docs/architecture.md](../../docs/architecture.md).
