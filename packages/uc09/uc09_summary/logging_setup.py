"""Structured JSON logging with a redaction guard.

Privacy rule for this component: application logs record identifiers, section
counts and timing. They never record summary content, question text, concept
or topic labels, resource titles, or which topics a named learner explored.

That rule is enforced twice - by discipline at every call site, and by
:func:`_redaction_processor`, which drops any event key on the deny-list before
the record is emitted. The processor exists because a privacy rule that relies
only on discipline is one careless log line away from being false.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

#: Event keys that must never reach a log sink, whatever a call site passes.
#: Anything carrying learner-visible prose belongs here.
DENIED_LOG_KEYS: frozenset[str] = frozenset(
    {
        "question_text",
        "questions",
        "question_log",
        "topic_label",
        "topic_labels",
        "topics",
        "concept_label",
        "concept_labels",
        "concepts",
        "explanation",
        "resource_title",
        "resource_titles",
        "citation",
        "citations",
        "summary",
        "summary_content",
        "content",
        "html",
        "body",
        "user_display_name",
        "rationale",
        "suggestion_label",
        "next_steps",
        "section_notes",
    }
)

_REDACTED = "[redacted]"

_configured = False


def _redaction_processor(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Drop denied keys before rendering. Structural backstop for the privacy rule."""
    for key in list(event_dict):
        if key.lower() in DENIED_LOG_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog once per process. Idempotent."""
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redaction_processor,
            structlog.processors.StackInfoRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    _configured = True


def reset_logging_for_tests() -> None:
    """Allow a test to reconfigure logging. Test-support only."""
    global _configured
    _configured = False
    structlog.reset_defaults()


def get_logger(name: str) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
