import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from voice_lab.catalog.sync import sync_catalog
from voice_lab.core.config import get_settings
from voice_lab.core.database import SessionLocal
from voice_lab.core.logging import configure_logging
from voice_lab.integrations.telephony.twilio.routes import router as twilio_router
from voice_lab.modules.calls.routes import router as calls_router
from voice_lab.modules.dashboard.routes import router as dashboard_router

configure_logging()
logger = logging.getLogger("voice_lab")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        async with SessionLocal() as db:
            await sync_catalog(db)
    except Exception:
        # Keep the API up (health shows the DB state); migrations may be pending.
        logger.exception("catalog sync failed; did you run `alembic upgrade head`?")
    yield


app = FastAPI(title="Voice AI Lab API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(calls_router)
app.include_router(dashboard_router)
app.include_router(twilio_router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Never leak provider messages (they can echo keys/numbers) to clients.
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.get("/health")
async def health() -> JSONResponse:
    try:
        async with SessionLocal() as db:
            await db.execute(text("select 1"))
        return JSONResponse({"status": "ok", "database": "ok"})
    except Exception:
        return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)
