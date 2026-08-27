"""Logs may carry counts and statuses - never analysis content."""

from __future__ import annotations

import json
import logging

import pytest

from tests.conftest import build_harness
from uc07.observability import (
    ALLOWED_LOG_FIELDS,
    JsonFormatter,
    LOGGER_NAME,
    log_event,
    sanitise,
)


#: Envelope keys the JSON formatter always adds.
ENVELOPE = {"event", "level", "logger", "timestamp"}


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    @property
    def payloads(self) -> list[dict]:
        return [json.loads(line) for line in self.lines]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def captured_logs():
    logger = logging.getLogger(LOGGER_NAME)
    handler = Capture()
    previous_handlers, previous_level, previous_propagate = (
        logger.handlers,
        logger.level,
        logger.propagate,
    )
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield handler
    finally:
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_disallowed_fields_are_dropped_before_they_reach_a_log_record(captured_logs):
    payload = log_event(
        "test_event",
        user_id="learner-001",
        interaction_count=14,
        topic_tag="contract_formation",
        gap_descriptions=["something sensitive"],
        question_text="what is consideration?",
        weak_topics=["negligence"],
    )
    assert payload == {
        "event": "test_event",
        "user_id": "learner-001",
        "interaction_count": 14,
    }
    assert "contract_formation" not in captured_logs.text
    assert "question" not in captured_logs.text
    assert "negligence" not in captured_logs.text


def test_sanitise_only_keeps_allowlisted_fields():
    assert sanitise({"user_id": "u", "topic_tag": "t"}) == {"user_id": "u"}
    assert "topic_tag" not in ALLOWED_LOG_FIELDS
    assert "description" not in ALLOWED_LOG_FIELDS
    assert "comment" not in ALLOWED_LOG_FIELDS


def test_report_generation_logs_counts_but_no_weak_topic_content(captured_logs):
    harness = build_harness("struggle_mixed")
    report = harness.service.current_report(harness.user_id).report
    assert report is not None

    text = captured_logs.text
    assert text, "expected structured log output"
    for gap in report.gaps:
        assert gap.topic_tag not in text
        assert gap.description not in text
    for record in harness.scenario.interactions[harness.user_id].records:
        assert record["topic_tag"] not in text

    events = {payload["event"] for payload in captured_logs.payloads}
    assert "report_available" in events
    report_log = next(
        payload for payload in captured_logs.payloads if payload["event"] == "report_available"
    )
    assert report_log["interaction_count"] == 14
    assert report_log["gap_count"] == 5
    assert report_log["signal_count_low_rating"] == 2
    assert report_log["source_status_feedback"] == "available"
    assert set(report_log) <= ALLOWED_LOG_FIELDS | ENVELOPE


def test_progress_logging_stays_within_the_allowlist(captured_logs):
    harness = build_harness("count_9")
    harness.service.progress(harness.user_id)
    payloads = [p for p in captured_logs.payloads if p["event"] == "progress_evaluated"]
    assert payloads
    for payload in payloads:
        assert set(payload) <= ALLOWED_LOG_FIELDS | ENVELOPE
        assert payload["interactions_remaining"] == 1


def test_no_log_line_ever_contains_a_feedback_comment(captured_logs):
    harness = build_harness("struggle_mixed")
    harness.service.current_report(harness.user_id)
    assert "comment" not in captured_logs.text
