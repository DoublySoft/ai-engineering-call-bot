"""Tests run against a separate `<db>_test` database on the lab Postgres.
Unit tests never touch it; integration tests skip if Postgres is down."""

import asyncio
import os

from voice_lab.core.config import get_settings

_base_url = get_settings().database_url
TEST_DB_URL = _base_url.rsplit("/", 1)[0] + "/" + _base_url.rsplit("/", 1)[1].split("?")[0] + "_test"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["ADMIN_API_TOKEN"] = "test-admin-token"
os.environ["OUTBOUND_ALLOWLIST"] = "+34600111222"
os.environ["PUBLIC_BASE_URL"] = "https://lab.example.test"
os.environ["TWILIO_ACCOUNT_SID"] = "AC" + "0" * 32
os.environ["TWILIO_AUTH_TOKEN"] = "test-twilio-token"
os.environ["TWILIO_PHONE_NUMBER"] = "+15005550006"
os.environ["OPENAI_API_KEY"] = "sk-test-not-real"
get_settings.cache_clear()


def _prepare_database() -> str | None:
    import asyncpg
    from alembic import command
    from alembic.config import Config

    async def create() -> None:
        admin_url = _base_url.replace("+asyncpg", "")
        conn = await asyncpg.connect(admin_url, timeout=3)
        try:
            db_name = TEST_DB_URL.rsplit("/", 1)[1]
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await conn.close()

    try:
        asyncio.run(create())
    except Exception as exc:  # Postgres not running
        return f"Postgres unavailable: {type(exc).__name__}"
    here = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    command.upgrade(cfg, "head")
    return None


DB_UNAVAILABLE: str | None = None
_prepared = False


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    global DB_UNAVAILABLE, _prepared
    import pytest

    if any("integration" in item.keywords for item in items) and not _prepared:
        DB_UNAVAILABLE = _prepare_database()
        _prepared = True
    if DB_UNAVAILABLE:
        skip = pytest.mark.skip(reason=DB_UNAVAILABLE)
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)
