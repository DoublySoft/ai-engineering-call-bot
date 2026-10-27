FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn voice_lab.main:app --host 0.0.0.0 --port 8000"]
