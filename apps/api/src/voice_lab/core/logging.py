import json
import logging
import sys
from datetime import UTC, datetime

# Keys that must never reach logs even if a caller passes them in `extra`.
_REDACT = {"api_key", "auth_token", "authorization", "password", "token", "stream_token"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in getattr(record, "fields", {}).items():
            payload[key] = "[redacted]" if key.lower() in _REDACT else value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def log(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: object) -> None:
    logger.log(level, msg, extra={"fields": fields})
