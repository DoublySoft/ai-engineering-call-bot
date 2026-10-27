"""Browser voice chat: same engines as phone calls, microphone as the transport."""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from voice_lab.core.config import Settings, get_settings
from voice_lab.core.database import SessionLocal, get_db
from voice_lab.core.errors import LabError
from voice_lab.core.security import new_stream_token, require_admin
from voice_lab.core.websocket import close_quietly
from voice_lab.engines.runner import SessionRunner
from voice_lab.modules.agents.service import resolve_session
from voice_lab.modules.browser.transport import BROWSER_INPUT_RATE, BROWSER_OUTPUT_RATE, BrowserTransport
from voice_lab.modules.calls.schemas import SessionConfig
from voice_lab.modules.sessions.config import check_provider_keys, pricing_snapshot
from voice_lab.observability.events import EventRecorder
from voice_lab.persistence.models import Call

logger = logging.getLogger(__name__)
router = APIRouter(tags=["browser"])
admin = [Depends(require_admin)]


class BrowserSessionRequest(BaseModel):
    config: SessionConfig = SessionConfig()


@router.post("/api/browser-sessions", dependencies=admin, status_code=201)
async def create_browser_session(
    request: BrowserSessionRequest, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    try:
        config, agent_cfg = await resolve_session(db, request.config, settings)
        check_provider_keys(config, settings)
    except LabError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    call = Call(
        direction="browser",
        status="ready",
        agent_configuration_id=agent_cfg.id,
        config_snapshot=config,
        pricing_snapshot=pricing_snapshot(config),
        started_at=datetime.now(UTC),
    )
    db.add(call)
    await db.commit()
    await EventRecorder(SessionLocal, call.id).emit(
        "call.created", provider="browser", direction="browser", llm_model=config["llm_model"], agent=config["agent"]["name"]
    )
    return {"id": str(call.id), "status": call.status}


@router.post("/api/calls/{call_id}/browser-token", dependencies=admin)
async def browser_token(call_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Issue a fresh single-use token for the audio WebSocket (one live connection per session)."""
    call = await db.get(Call, call_id)
    if call is None or call.direction != "browser":
        raise HTTPException(404)
    if call.status != "ready":
        raise HTTPException(409, f"session is {call.status}; start a new browser session")
    if call.config_snapshot.get("voice_provider") == "retell":
        raise HTTPException(409, "Retell sessions connect through /retell-web-call")
    call.stream_token = new_stream_token()
    await db.commit()
    return {
        "token": call.stream_token,
        "path": f"/browser/audio?call_id={call.id}&token={call.stream_token}",
        "input_sample_rate": BROWSER_INPUT_RATE,
        "output_sample_rate": BROWSER_OUTPUT_RATE,
    }


@router.websocket("/browser/audio")
async def browser_audio(ws: WebSocket, call_id: str = "", token: str = "") -> None:
    await ws.accept()
    runner = SessionRunner()
    transport = BrowserTransport(ws)
    end_reason = "client_stopped"
    if not await runner.open(call_id, token, transport):
        await ws.close(code=1008)
        return
    try:
        while ws.application_state == WebSocketState.CONNECTED:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                end_reason = "websocket_disconnected"
                break
            if message.get("bytes"):
                data = message["bytes"]
                await runner.feed(np.frombuffer(data[: len(data) - len(data) % 2], dtype="<i2").astype(np.int16))
            elif message.get("text"):
                control = json.loads(message["text"])
                if control.get("type") == "mark":
                    await runner.playback_done(str(control.get("name", "")))
                elif control.get("type") == "stop":
                    break
    except WebSocketDisconnect:
        end_reason = "websocket_disconnected"
    except Exception:
        end_reason = "error"
        logger.exception("browser audio session crashed")
    finally:
        await runner.close(end_reason)
        await close_quietly(ws)
