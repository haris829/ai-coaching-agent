"""Structured JSON logging on the standard library.

``structlog`` is the first choice in the platform stack; where it is not
installed this module provides the sanctioned alternative -- stdlib ``logging``
with a JSON formatter -- behind the same ``get_logger`` call, so no module in
this component depends on which of the two is present.

Log records carry the event name as the message and everything else as
structured fields. No user-supplied free text is ever formatted into the
message string.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_LOGGER_NAME_ROOT = "uc08"
_CONFIGURED = False

_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a log record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = _jsonable(value)
        if record.exc_info:
            payload["exception_type"] = getattr(record.exc_info[0], "__name__", "unknown")
        return json.dumps(payload, default=str, sort_keys=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def configure_logging(level: str = "INFO", stream: Any | None = None) -> None:
    """Attach the JSON formatter once. Idempotent."""
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME_ROOT)
    logger.setLevel(level.upper())
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``uc08`` root."""
    if not name.startswith(_LOGGER_NAME_ROOT):
        name = f"{_LOGGER_NAME_ROOT}.{name}"
    return logging.getLogger(name)
