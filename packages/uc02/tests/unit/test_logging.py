"""Structured logging: what must be logged, and what must never be (section 15)."""

from __future__ import annotations

import json
import logging

import pytest

from uc02.infrastructure.logging.setup import JsonFormatter, user_reference
from uc02.infrastructure.providers.mocks import CoursesScenario, HistoryScenario, NaricScenario
from tests.fixtures.factories import make_harness, make_identity


class CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture()
def captured_logs():
    logger = logging.getLogger("uc02-test")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    handler = CapturingHandler()
    logger.addHandler(handler)
    yield logger, handler
    logger.handlers.clear()


async def _run(logger, **scenarios):
    harness = make_harness(**scenarios)
    harness.service._log = logger
    return (await harness.service.initialize(make_identity(user_id="learner-secret"))).context


async def test_session_initialisation_is_logged(captured_logs):
    logger, handler = captured_logs
    await _run(logger)
    events = [line["event"] for line in handler.lines]
    assert "context.initialize.start" in events
    assert "context.assembly.complete" in events


async def test_every_provider_status_and_latency_is_logged(captured_logs):
    logger, handler = captured_logs
    await _run(logger)
    provider_lines = [
        line for line in handler.lines if line["event"] == "context.provider.result"
    ]
    assert {line["source"] for line in provider_lines} == {
        "naric",
        "courses",
        "legal_profile",
        "question_history",
    }
    for line in provider_lines:
        assert "status" in line
        assert "duration_ms" in line
        assert "error_category" in line
        assert "timestamp" in line
        assert line["session_id"] == "sess-fixture-1"


async def test_every_fallback_is_logged(captured_logs):
    logger, handler = captured_logs
    await _run(logger, naric=NaricScenario.UNAVAILABLE, courses=CoursesScenario.UNAVAILABLE)
    fallbacks = [line for line in handler.lines if line["event"] == "context.fallback.applied"]
    sources = {line["source"] for line in fallbacks}
    assert sources == {"naric", "courses"}
    assert any("defaulted to 5" in line["fallback"] for line in fallbacks)


async def test_the_final_assembly_result_is_logged_with_statuses(captured_logs):
    logger, handler = captured_logs
    await _run(logger)
    final = next(line for line in handler.lines if line["event"] == "context.assembly.complete")
    assert final["statuses"] == {
        "naric": "available",
        "courses": "available",
        "legal_profile": "available",
        "question_history": "available",
    }
    assert final["personalization_available"] is True
    assert final["context_version"] == "uc02.context.v1"
    assert isinstance(final["duration_ms"], int)


async def test_question_text_never_reaches_a_log_line(captured_logs):
    logger, handler = captured_logs
    context = await _run(logger, history=HistoryScenario.FEWER_THAN_20)
    excerpt = context.question_history.items[0].text_excerpt
    assert excerpt  # the text exists server-side
    blob = json.dumps(handler.lines)
    assert excerpt not in blob
    assert "Mock question" not in blob


async def test_legal_profile_contents_never_reach_a_log_line(captured_logs):
    logger, handler = captured_logs
    context = await _run(logger)
    blob = json.dumps(handler.lines)
    for speciality in context.legal_profile.speciality_areas:
        assert speciality not in blob
    for case_type in context.legal_profile.case_type_preferences:
        assert case_type not in blob
    assert context.legal_profile.practice_area not in blob


async def test_the_raw_user_id_is_replaced_by_a_one_way_reference(captured_logs):
    logger, handler = captured_logs
    await _run(logger)
    blob = json.dumps(handler.lines)
    assert "learner-secret" not in blob
    expected = user_reference("learner-secret", "test-salt")
    assert expected in blob


def test_the_formatter_redacts_forbidden_keys_even_if_a_caller_passes_them():
    handler = CapturingHandler()
    logger = logging.getLogger("uc02-redaction-test")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info(
        "some.event",
        extra={
            "user_id": "raw-user",
            "text": "what is consideration?",
            "api_key": "sk-live-123",
            "source": "naric",
        },
    )
    line = handler.lines[0]
    assert line["user_id"] == "[redacted]"
    assert line["text"] == "[redacted]"
    assert line["api_key"] == "[redacted]"
    assert line["source"] == "naric"


def test_every_log_line_is_a_single_json_object():
    handler = CapturingHandler()
    logger = logging.getLogger("uc02-json-test")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("event.name", extra={"session_id": "s1"})
    line = handler.lines[0]
    assert set(line) >= {"timestamp", "level", "logger", "event", "session_id"}


def test_user_reference_is_stable_and_not_reversible():
    first = user_reference("learner-1", "salt")
    assert first == user_reference("learner-1", "salt")
    assert first != user_reference("learner-2", "salt")
    assert first != user_reference("learner-1", "other-salt")
    assert "learner-1" not in first
