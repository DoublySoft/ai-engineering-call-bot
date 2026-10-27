from fastapi import WebSocket
from starlette.websockets import WebSocketState


async def close_quietly(ws: WebSocket, code: int = 1000) -> None:
    """Close our side if still open; the peer may already be gone (normal on hang-up)."""
    if ws.application_state != WebSocketState.CONNECTED:
        return
    try:
        await ws.close(code=code)
    except Exception:  # ClientDisconnected / WebSocketDisconnect: nothing left to close
        pass
