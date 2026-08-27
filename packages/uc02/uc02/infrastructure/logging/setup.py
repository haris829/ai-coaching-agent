"""Structured JSON logging on the stdlib.

Every record is one JSON object. UC-02 logs session initialisation, each
provider's status and latency, every fallback applied, and the final assembly
result.

Never logged: question text, full legal profiles, complete history payloads,
credentials, keys. ``user_reference`` replaces ``user_id`` in every log line and
is a salted one-way digest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

LOGGER_NAME = "uc02"

#: Keys that must never reach a log line even if a caller passes them.
FORBIDDEN_LOG_KEYS = frozenset(
    {
        "text",
        "question_text",
        "text_excerpt",
        "questions",
        "history_payload",
        "legal_profile",
        "speciality_areas",
        "case_type_preferences",
        "authorization",
        "api_key",
        "token",
        "secret",
        "password",
        "user_id",  # use user_reference instead
    }
)

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def user_reference(user_id: str, salt: str) -> str:
    """A stable, non-reversible reference for a user id, safe to log."""
    digest = hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()
    return f"uref_{digest[:16]}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if key in FORBIDDEN_LOG_KEYS:
                payload[key] = "[redacted]"
                continue
            payload[key] = _coerce(value)
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str, sort_keys=True)


def _coerce(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    return str(value)


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            handler.setFormatter(JsonFormatter())
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
