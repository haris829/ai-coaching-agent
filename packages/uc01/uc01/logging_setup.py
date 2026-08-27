"""Structured logging.

One JSON object per line, with a stable envelope, so that shipping these logs into a
company pipeline later is a formatter/handler change rather than a code change.

Conventions used throughout UC-01:

* the message is a dotted event name (``session.open.degraded``), not a sentence;
* structured fields go in ``extra={"uc01": {...}}``;
* technical detail belongs in the log, never in an API response.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonLogFormatter(logging.Formatter):
    """Minimal JSON formatter with a UC-01 envelope."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
            "use_case": "UC-01",
        }
        context = getattr(record, "uc01", None)
        if isinstance(context, Mapping):
            payload["context"] = {str(key): _safe(value) for key, value in context.items()}

        extras = {
            key: _safe(value)
            for key, value in record.__dict__.items()
            if key not in _RESERVED and key != "uc01"
        }
        if extras:
            payload.setdefault("context", {}).update(extras)

        if record.exc_info:
            # Full traceback stays server-side; it never enters an API response.
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    return str(value)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream=sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s")
        )
    root.addHandler(handler)


__all__ = ["JsonLogFormatter", "configure_logging"]
