"""JSON structured logging with a privacy allowlist.

Logs may contain user id, counts, timings and source statuses. Logs must never
contain the learner's weak topics, report contents, gap descriptions, feedback
comments or question text (which UC-07 never reads in the first place).

The allowlist below is the enforcement point: :func:`log_event` drops any field
that is not explicitly permitted, so a careless call site cannot leak analysis
content. ``uc07`` never calls ``logger.info`` with free-text interpolation of
domain values - only through this function.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

LOGGER_NAME = "uc07"

#: The only field names allowed to appear in a log record.
ALLOWED_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "event",
        "user_id",
        "http_status",
        "error_code",
        "port",
        "duration_ms",
        "threshold",
        "threshold_status",
        "interactions_completed",
        "interactions_remaining",
        "interaction_count",
        "provider_reported_count",
        "duplicates_discarded",
        "other_user_records_discarded",
        "session_count",
        "topic_area_count",
        "gap_count",
        "struggle_gap_count",
        "unexplored_gap_count",
        "signal_count_explain_differently",
        "signal_count_follow_up",
        "signal_count_low_rating",
        "rejected_gap_count",
        "recommendation_count",
        "recommendation_status",
        "recommendations_rejected_count",
        "source_status_interactions",
        "source_status_feedback",
        "source_status_profile",
        "source_status_courses",
        "report_refreshed",
        "report_id",
        "unexplored_analysis_state",
        "provider_kind",
    }
)


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter: no message templating, no stack traces emitted."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname.lower(),
            "logger": record.name,
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        fields = getattr(record, "uc07_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        else:
            payload["event"] = record.getMessage()
        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Install the JSON handler on the ``uc07`` logger (idempotent)."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.handlers = [handler]
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def sanitise(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop every field that is not on the allowlist."""
    return {key: value for key, value in fields.items() if key in ALLOWED_LOG_FIELDS}


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> dict[str, Any]:
    """Emit one structured event. Returns the sanitised payload (for tests)."""
    payload = {"event": event, **sanitise(fields)}
    get_logger().log(level, event, extra={"uc07_fields": payload})
    return payload
