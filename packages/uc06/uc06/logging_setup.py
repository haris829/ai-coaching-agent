"""Structured logging with an allowlisted field set.

Privacy here is structural, not procedural. A caller cannot log case fact text or
question text by mistake, because `emit` only serialises keys on
SAFE_LOG_KEYS - anything else is replaced by a marker naming the dropped key. The
message itself is a fixed event name chosen from a closed set, never interpolated
content.

stdlib logging with a JSON formatter, per the stack note (structlog optional).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Iterable, Mapping

LOGGER_NAME = "uc06"

#: Identifiers, statuses, counts and classifications only. No free text, no
#: question text, no case fact text, no prompt content, no provider names.
SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {
        "event",
        "request_id",
        "interaction_id",
        "response_id",
        "session_id",
        "user_id",
        "case_file_id",
        "audit_id",
        "incident_id",
        "mode",
        "question_class",
        "topic_tag",
        "naric_level",
        "naric_level_source",
        "explanation_profile",
        "guard_triggered",
        "guard_rule_id",
        "matched_rule_ids",
        "disclaimer_present",
        "rating_state",
        "case_file_status",
        "learner_context_status",
        "source_status",
        "fact_ids",
        "fact_count",
        "outcome",
        "error_code",
        "reason_code",
        "severity",
        "halted",
        "retryable",
        "port",
        "duration_ms",
        "prompt_id",
        "prompt_version",
        "action",
        "kind",
        "detail_code",
        "status_code",
        "path",
        "rejected_fields",
        "legal_test_version",
    }
)

#: Keys that must never be logged even if someone adds them to SAFE_LOG_KEYS by
#: accident. Belt and braces: the sanitiser drops these unconditionally.
NEVER_LOG_KEYS: frozenset[str] = frozenset(
    {
        "question",
        "question_text",
        "content",
        "text",
        "fact_text",
        "facts",
        "case_facts",
        "prompt",
        "system_prompt",
        "system_instructions",
        "generated",
        "generation",
        "answer",
        "disclaimer",
        "api_key",
        "authorization",
        "charges",
        "evidence",
        "legislation",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "uc06_fields", None)
        if isinstance(fields, Mapping):
            payload.update(fields)
        if record.exc_info:
            # Type only. No traceback, no exception message: internal exception
            # text can carry case content and never reaches a log sink.
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "unknown"
        return json.dumps(payload, sort_keys=True, default=str)


def sanitise(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Drop everything not explicitly safe. Never raises: a logging call must not
    take down a request, and a dropped field is visible in the output."""
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key in NEVER_LOG_KEYS or key not in SAFE_LOG_KEYS:
            clean[f"dropped_field.{key}"] = "<redacted-by-policy>"
            continue
        if isinstance(value, (list, tuple)):
            clean[key] = [str(v) for v in value]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


class EventLogger:
    """The only logging entry point in UC-06."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _log(self, level: int, event: str, **fields: Any) -> None:
        self._logger.log(level, event, extra={"uc06_fields": sanitise(fields)})

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, **fields)

    def critical(self, event: str, **fields: Any) -> None:
        self._log(logging.CRITICAL, event, **fields)


#: Marks the handler this module owns, so reconfiguring never removes a handler
#: installed by someone else (an operator's sink, a test's capture buffer).
_OWNED = "_uc06_owned_handler"


def configure_logging(level: int = logging.INFO, stream: Any = None) -> None:
    root = logging.getLogger(LOGGER_NAME)
    for existing in [h for h in root.handlers if getattr(h, _OWNED, False)]:
        root.removeHandler(existing)
    handler = logging.StreamHandler(stream or sys.stdout)
    setattr(handler, _OWNED, True)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def get_logger(suffix: str = "") -> EventLogger:
    name = f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME
    return EventLogger(logging.getLogger(name))


def known_events() -> Iterable[str]:
    return (
        "case_coaching.requested",
        "case_coaching.access_verified",
        "case_coaching.access_denied",
        "case_coaching.origin_rejected",
        "case_coaching.case_file_loaded",
        "case_coaching.case_file_unavailable",
        "case_coaching.context_defaulted",
        "case_coaching.guard_redirected",
        "case_coaching.generation_failed",
        "case_coaching.fabricated_fact_reference",
        "case_coaching.output_prediction_blocked",
        "case_coaching.answered",
        "case_coaching.session_halted",
        "case_coaching.halt_blocked_request",
        "disclaimer.boundary_failure",
        "security.incident_recorded",
        "audit.case_linked_coaching",
    )
