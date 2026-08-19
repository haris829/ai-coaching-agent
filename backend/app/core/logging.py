"""Structured logging.

The workspace had no pre-existing logging architecture, so this defines a small JSON-lines
formatter over the standard library ``logging`` module. Technical detail (stack traces,
database errors, CSV parse failures) is logged here; the HTTP layer never returns it.

Context fields are nested under a single ``ctx`` key before they reach ``LogRecord``. That
matters: ``logging`` raises ``KeyError`` if an ``extra`` key collides with a reserved record
attribute, and natural field names for this domain — ``filename``, ``module``, ``name`` — are
all reserved. Nesting makes any context key safe, then the formatter flattens it back out.

Every module obtains its logger via ``get_logger(__name__)``, so a host application that
already configures ``logging`` only needs to skip ``configure_logging()``.

DATA MINIMISATION
-----------------
``JsonFormatter`` drops any context key on the deny-list below outright. See the comment on
``_FORBIDDEN_CONTEXT_FRAGMENTS``: the answer key and a learner's coaching conversation must never
reach a log sink, and a deny-list is the net under call sites that are already careful.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import MutableMapping
from typing import Any

from app.core.config import settings

#: The single record attribute this module writes context into.
CONTEXT_KEY = "ctx"

#: Context keys that must never reach a log sink.
#:
#: Two things in this system are forbidden from being logged, and both arrived with UC-07: the
#: **answer key**, which the AI coach is architecturally forbidden from seeing and which a log line
#: would route around that boundary, and the **coaching conversation**, which is a learner thinking
#: aloud about something they got wrong. Learner answers and question text are on the list for the
#: same reason.
#:
#: Every call site is already expected to log identifiers, counts and codes only — see
#: ``app.modules.coaching.domain.redaction``, which builds the permitted context dictionaries. This
#: is the net under that: a careless ``extra={"answer_key": …}`` or ``extra={"message": …}`` cannot
#: leak even by accident. Matching is on the *substring*, so ``correct_option_id``,
#: ``learner_answer_text`` and ``coach_message`` are all covered without enumerating them.
_FORBIDDEN_CONTEXT_FRAGMENTS: tuple[str, ...] = (
    "answer",
    "correct",
    "solution",
    "explanation",
    "question_text",
    "prompt_text",
    "option_text",
    "message",
    "transcript",
    "conversation",
    "content",
    "learner_name",
    "learner_email",
    "coaching_context",
)

#: Keys that contain a forbidden fragment but are safe and operationally necessary: they hold
#: counts, statuses, booleans and error codes, never content.
_ALLOWED_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "answered_count",
        "incorrect_count",
        "message_count",
        "learner_message_count",
        "coach_message_count",
        "explanation_mode",
        "direct_explanation_available",
        "answer_key_excluded",
        "contamination_detected",
        "contamination_findings",
        "prompt_version",
    }
)

REDACTED = "[redacted]"


def _permitted(key: str) -> bool:
    if key in _ALLOWED_CONTEXT_KEYS:
        return True
    lowered = key.lower()
    return not any(fragment in lowered for fragment in _FORBIDDEN_CONTEXT_FRAGMENTS)


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object, flattening the nested context."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        context = getattr(record, CONTEXT_KEY, None)
        if isinstance(context, dict):
            for key, value in context.items():
                # Never let a context key shadow the envelope fields above.
                target = key if key not in payload else f"ctx_{key}"
                payload[target] = _safe(value) if _permitted(key) else REDACTED

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str)


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return str(value)


class ContextLogger(logging.LoggerAdapter):
    """Moves ``extra={...}`` under ``ctx`` so no key can collide with a reserved attribute."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        extra = kwargs.get("extra")
        if extra:
            kwargs["extra"] = {CONTEXT_KEY: dict(extra)}
        return msg, kwargs


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    # Keep pytest output readable; failures are asserted through HTTP responses.
    root.setLevel(logging.CRITICAL if settings.is_test else settings.log_level.upper())

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> ContextLogger:
    configure_logging()
    return ContextLogger(logging.getLogger(name), {})
