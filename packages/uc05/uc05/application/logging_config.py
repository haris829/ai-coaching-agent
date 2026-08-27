"""Structured logging with a privacy allow-list.

``structlog`` is not available in this environment, so this is stdlib logging
with a JSON formatter -- explicitly permitted by the brief.  The deviation is
recorded in the final report.

Privacy is enforced by construction, not by discipline.  ``log_event`` accepts
only keys on ``ALLOWED_FIELDS``; anything else raises immediately, in tests and
in production alike.  A developer cannot add ``question_text`` to a log line by
accident, because the logger refuses it.

Socratic dialogues record a learner's reasoning, including where they were
wrong.  In a professional context that is sensitive: it is career-relevant
information about a practising professional's competence.  It belongs in the
dialogue store, which has an owner and an access check, and not in an
application log, which is typically shipped to an aggregator with far broader
read access and a far longer retention.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

LOGGER_NAME = "uc05"

#: The complete set of fields UC-05 may log.  Deliberately an allow-list.
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "event",
        "session_id",
        "dialogue_id",
        "interaction_id",
        "user_id",
        "exchange_number",
        "exchanges_used",
        "exchanges_remaining",
        "exchange_cap",
        "response_kind",
        "resolution",
        "state",
        "previous_state",
        "transition",
        "dialogue_event",
        "intent",
        "intent_rule",
        "matched_phrase",
        "naric_level",
        "naric_level_source",
        "explanation_profile",
        "source_status",
        "topic_tag",
        "mode_enabled",
        "mode_source",
        "port",
        "error_type",
        "retryable",
        "duration_ms",
        "over_p95_target",
        "prompt_version",
        "loop_similarity",
        "loop_matched_exchange",
        "provider_key",
        "outcome",
        "count",
        "reason",
    }
)

#: Fields that are never loggable, named explicitly so the failure message is
#: useful rather than merely "unknown field".
FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "question_text",
        "question",
        "learner_response",
        "message",
        "guiding_question",
        "answer",
        "reasoning_chain",
        "prompt",
        "system_instruction",
        "practice_area",
    }
)


class DisallowedLogField(Exception):
    """Raised when a caller tries to log something outside the allow-list."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        structured = getattr(record, "uc05", None)
        if isinstance(structured, dict):
            payload.update(structured)
        else:
            payload["message"] = record.getMessage()
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured log line, refusing anything off the allow-list."""
    forbidden = FORBIDDEN_FIELDS & set(fields)
    if forbidden:
        raise DisallowedLogField(
            f"refusing to log learner or prompt content: {sorted(forbidden)}"
        )
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise DisallowedLogField(
            f"field(s) {sorted(unknown)} are not on the logging allow-list; "
            f"add them to ALLOWED_FIELDS only if they carry no learner content"
        )
    payload = {"event": event, **{k: v for k, v in fields.items() if v is not None}}
    get_logger().log(level, event, extra={"uc05": payload})
