import hashlib
import hmac
import re
import secrets

from fastapi import Header, HTTPException, status

from voice_lab.core.config import get_settings

E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def require_admin(authorization: str = Header(default="")) -> None:
    """Every /api endpoint is administrative in this lab: transcripts included."""
    expected = get_settings().admin_api_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ADMIN_API_TOKEN is not configured")
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin token")


def normalize_e164(number: str) -> str:
    cleaned = re.sub(r"[\s\-().]", "", number)
    if not E164.match(cleaned):
        raise ValueError("phone number must be in E.164 format, e.g. +34600111222")
    return cleaned


def new_stream_token() -> str:
    return secrets.token_urlsafe(24)


def events_token(session_id: str) -> str:
    """Short capability token so the browser can open the live-events WebSocket
    without ever holding the admin token."""
    key = get_settings().admin_api_token.encode()
    return hmac.new(key, f"events:{session_id}".encode(), hashlib.sha256).hexdigest()


def verify_events_token(session_id: str, token: str) -> bool:
    return bool(get_settings().admin_api_token) and hmac.compare_digest(events_token(session_id), token)
