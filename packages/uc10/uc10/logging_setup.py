"""Structured logging with learner content structurally excluded.

This component stores question text, response text and learner comments because the
improvement pipeline requires them.  None of it may ever reach a log line.  Three
defences, in order:

1. Call sites use :func:`event` helpers that take identifiers, not records.
2. A redaction processor replaces any denied key's value with ``[redacted]``.
3. A processor refuses to serialise domain records or unknown objects, emitting their
   type name instead, so no accidental ``logger.info("saved", rating=record)`` can leak.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import is_dataclass
from typing import Any

import structlog
from pydantic import BaseModel

#: Keys whose values are learner content and must never be logged.
DENIED_LOG_KEYS: frozenset[str] = frozenset(
    {
        "question_text",
        "response_text",
        "comment",
        "comment_text",
        "question",
        "response",
        "answer_text",
        "text",
        "body",
        "payload",
        "raw",
        "raw_response",
        "content",
    }
)

REDACTED = "[redacted]"


def redact_learner_content(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in DENIED_LOG_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def refuse_rich_objects(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Never serialise a record. Emit its type name so the leak cannot happen by accident."""
    for key, value in list(event_dict.items()):
        if isinstance(value, BaseModel) or (is_dataclass(value) and not isinstance(value, type)):
            event_dict[key] = f"<{type(value).__name__}>"
        elif isinstance(value, (list, tuple)):
            event_dict[key] = [
                f"<{type(v).__name__}>"
                if isinstance(v, BaseModel) or (is_dataclass(v) and not isinstance(v, type))
                else v
                for v in value
            ]
        elif isinstance(value, dict):
            event_dict[key] = {
                k: (REDACTED if str(k).lower() in DENIED_LOG_KEYS else v) for k, v in value.items()
            }
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to render JSON through stdlib logging.

    Rendering through stdlib means the privacy test can capture every line the whole
    application emits, including anything a library logs.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            refuse_rich_objects,
            redact_learner_content,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
