"""Knowledge-gap tracking and coaching activity logging (§21, §22, §33).

Both are outbound streams: UC-07 writes them and never reads them back. Two properties matter more
than the contents — that they record the right *shape* of thing, and that neither can break a
learner's coaching session by failing.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import REDACTED, JsonFormatter
from app.modules.coaching.domain.enums import CoachingMode
from app.modules.coaching.domain.redaction import session_context
from app.modules.coaching.integration.activity import CoachingActivityType
from tests.coaching.world import (
    ATTEMPT_1,
    LEARNER,
    MULTI_CORRECT_ANSWER_TEXT,
    Q_MULTI,
    Q_SCENARIO,
    World,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Knowledge gaps (§21)
# ---------------------------------------------------------------------------


async def test_opening_coaching_records_the_topic_as_a_gap(world: World) -> None:
    world.given_standard_quiz()

    await world.start(Q_MULTI)

    assert world.gaps.topics == ["Reporting concerns"]


async def test_the_gap_links_the_learner_attempt_question_and_session(world: World) -> None:
    world.given_standard_quiz()

    started = await world.start(Q_MULTI)
    event = world.gaps.events[0]

    assert event.learner_id == LEARNER
    assert event.attempt_id == ATTEMPT_1
    assert event.course_id == "course-1"
    assert event.question_id == Q_MULTI
    assert event.session_id == started.state.session.session_id
    assert event.occurred_at == "2026-02-01T08:00:00Z"
    assert event.source == "COACHING_SESSION_STARTED"


async def test_resuming_does_not_record_a_second_gap(world: World) -> None:
    """One question, one gap — however long the learner spends on it (§21)."""
    world.given_standard_quiz()

    await world.start(Q_MULTI)
    await world.start(Q_MULTI)
    await world.start(Q_MULTI)

    assert len(world.gaps.events) == 1


async def test_exchanges_do_not_record_further_gaps(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    await world.exchange_n_times(session_id, 3)

    assert len(world.gaps.events) == 1


async def test_each_reviewed_question_records_its_own_topic(world: World) -> None:
    world.given_standard_quiz()

    await world.start(Q_MULTI)
    await world.start(Q_SCENARIO)

    assert world.gaps.topics == ["Reporting concerns", "Escalation"]


async def test_a_gap_carries_no_answer_bearing_content(world: World) -> None:
    world.given_standard_quiz()

    await world.start(Q_MULTI)
    serialised = json.dumps(world.gaps.events[0].as_dict()).lower()

    assert MULTI_CORRECT_ANSWER_TEXT.lower() not in serialised
    assert "correct" not in serialised
    assert "explanation" not in serialised


async def test_a_failing_gap_tracker_does_not_break_coaching(world: World) -> None:
    world.given_standard_quiz()
    world.gaps.raises = True

    started = await world.start(Q_MULTI)

    assert started.coaching_available is True
    assert len(started.state.transcript.messages) == 1


# ---------------------------------------------------------------------------
# Activity logging (§22)
# ---------------------------------------------------------------------------


async def test_starting_a_session_is_recorded(world: World) -> None:
    world.given_standard_quiz()

    started = await world.start(Q_MULTI)
    events = world.activity.of_type(CoachingActivityType.SESSION_STARTED.value)

    assert len(events) == 1
    assert events[0].session_id == started.state.session.session_id
    assert events[0].topic == "Reporting concerns"
    assert events[0].mode == "SOCRATIC"


async def test_each_exchange_is_recorded_with_its_count(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    await world.exchange_n_times(session_id, 3)
    events = world.activity.of_type(CoachingActivityType.EXCHANGE_COMPLETED.value)

    assert [event.exchange_count for event in events] == [1, 2, 3]


async def test_mode_changes_and_completion_are_recorded(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 5)

    await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)
    await world.coaching.complete_session(learner_id=LEARNER, session_id=session_id)

    assert len(world.activity.of_type(CoachingActivityType.MODE_CHANGED.value)) == 1
    assert len(world.activity.of_type(CoachingActivityType.SESSION_COMPLETED.value)) == 1


async def test_a_failure_is_recorded_with_its_code_only(world: World) -> None:
    world.given_standard_quiz()
    world.llm.go_offline()

    await world.start(Q_MULTI)
    failures = world.activity.of_type(CoachingActivityType.SESSION_FAILED.value)

    assert len(failures) == 1
    assert failures[0].failure_code == "COACHING_SERVICE_UNAVAILABLE"


async def test_an_activity_event_carries_no_conversation(world: World) -> None:
    """§22: no answer keys, no correct answers, no full conversation."""
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.say(session_id, "A very memorable and private thing the learner typed.")

    serialised = json.dumps([event.as_dict() for event in world.activity.events]).lower()

    assert "memorable and private" not in serialised
    assert MULTI_CORRECT_ANSWER_TEXT.lower() not in serialised
    assert "content" not in serialised


async def test_a_failing_activity_log_does_not_break_coaching(world: World) -> None:
    world.given_standard_quiz()
    world.activity.raises = True

    started = await world.start(Q_MULTI)
    exchange = await world.say(started.state.session.session_id, "Why not B?")

    assert started.coaching_available is True
    assert exchange.coaching_available is True
    assert exchange.state.session.exchange_count == 1


# ---------------------------------------------------------------------------
# The log formatter's deny-list (§22)
# ---------------------------------------------------------------------------


def _format(context: dict[str, object]) -> dict[str, object]:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "coaching.test", None, None)
    record.ctx = context  # type: ignore[attr-defined]
    return json.loads(JsonFormatter().format(record))


def test_the_formatter_drops_answer_bearing_context() -> None:
    formatted = _format(
        {
            "session_id": "session-1",
            "answer_key": {"correct_option_ids": ["A"]},
            "correct_answer_text": MULTI_CORRECT_ANSWER_TEXT,
        }
    )

    assert formatted["session_id"] == "session-1"
    assert formatted["answer_key"] == REDACTED
    assert formatted["correct_answer_text"] == REDACTED


def test_the_formatter_drops_conversation_content() -> None:
    formatted = _format({"message_content": "what the learner typed", "transcript": ["a", "b"]})

    assert formatted["message_content"] == REDACTED
    assert formatted["transcript"] == REDACTED


def test_the_formatter_keeps_operational_counts() -> None:
    formatted = _format({"message_count": 4, "direct_explanation_available": True})

    assert formatted["message_count"] == 4
    assert formatted["direct_explanation_available"] is True


async def test_the_session_log_context_is_identifiers_and_counts_only(world: World) -> None:
    world.given_standard_quiz()
    session = (await world.start(Q_MULTI)).state.session

    context = session_context(session)

    assert set(context) == {
        "session_id",
        "learner_id",
        "attempt_id",
        "course_id",
        "question_id",
        "topic",
        "mode",
        "status",
        "exchange_count",
        "direct_explanation_available",
        "revision",
    }
    # Everything in it survives the formatter's deny-list, so the log line is actually useful.
    formatted = _format(dict(context))
    assert REDACTED not in formatted.values()
