"""Small domain units that the flow tests exercise only in passing.

Answer description, transcript mechanics and the repository's identity rules. Each is short, but
each holds a rule that would be expensive to discover only through an end-to-end failure.
"""

from __future__ import annotations

import pytest

from app.core.coercion import enum_values, parse_enum
from app.modules.coaching.domain.answers import describe_learner_answer
from app.modules.coaching.domain.enums import CoachingSessionStatus, MessageRole
from app.modules.coaching.domain.errors import (
    CoachingSessionNotFoundError,
    DuplicateCoachingSessionError,
)
from app.modules.coaching.domain.session import new_session
from app.modules.coaching.domain.transcript import (
    ChatMessage,
    CoachingTranscript,
    build_messages,
    to_history,
)
from app.modules.coaching.integration.uc03 import LearnerAnswer, QuestionType
from app.modules.coaching.repositories.in_memory import InMemoryCoachingSessionRepository
from tests.coaching.world import make_answer, make_delivered_question

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Describing the learner's answer (§11, §12)
# ---------------------------------------------------------------------------


def test_an_unanswered_question_is_described_as_blank() -> None:
    question = make_delivered_question(question_id="q", position=1)

    view = describe_learner_answer(question, None)

    assert view.answered is False
    assert view.summary == "No answer was submitted."


def test_a_single_choice_answer_is_described_by_its_label() -> None:
    question = make_delivered_question(
        question_id="q", position=1, options=(("A", "Tell the lead"), ("B", "Do nothing"))
    )
    answer = make_answer(
        question_id="q", response={"type": "SINGLE_CHOICE", "selected_option_id": "B"}
    )

    view = describe_learner_answer(question, answer)

    assert view.selected_option_ids == ("B",)
    assert view.summary == "The learner selected: Do nothing"


def test_an_option_that_was_never_delivered_resolves_to_no_label() -> None:
    """The coach should not be told about an option that was not on screen."""
    question = make_delivered_question(
        question_id="q", position=1, options=(("A", "Tell the lead"),)
    )
    answer = make_answer(
        question_id="q", response={"type": "SINGLE_CHOICE", "selected_option_id": "Z"}
    )

    view = describe_learner_answer(question, answer)

    assert view.selected_option_labels == ()
    assert view.summary == "The learner selected option(s): Z"


def test_a_drag_to_order_answer_is_described_in_the_learners_order() -> None:
    question = make_delivered_question(
        question_id="q",
        position=1,
        question_type=QuestionType.DRAG_TO_ORDER,
        order_items=(("s1", "Observe"), ("s2", "Record"), ("s3", "Report")),
    )
    answer = make_answer(
        question_id="q",
        response={"type": "DRAG_TO_ORDER", "ordered_item_ids": ["s3", "s1", "s2"]},
    )

    view = describe_learner_answer(question, answer)

    assert view.ordered_item_labels == ("Report", "Observe", "Record")
    assert view.summary == "The learner ordered the steps as: Report → Observe → Record"


def test_an_order_of_unknown_items_falls_back_to_ids() -> None:
    question = make_delivered_question(
        question_id="q", position=1, question_type=QuestionType.DRAG_TO_ORDER
    )
    answer = make_answer(
        question_id="q", response={"type": "DRAG_TO_ORDER", "ordered_item_ids": ["s3", "s1"]}
    )

    view = describe_learner_answer(question, answer)

    assert view.summary == "The learner ordered the items as: s3 → s1"


def test_a_true_false_answer_is_described_as_the_value_given() -> None:
    question = make_delivered_question(
        question_id="q", position=1, question_type=QuestionType.TRUE_FALSE
    )
    answer = make_answer(question_id="q", response={"type": "TRUE_FALSE", "value": False})

    view = describe_learner_answer(question, answer)

    assert view.boolean_value is False
    assert view.summary == "The learner answered False."


def test_free_text_is_carried_verbatim() -> None:
    question = make_delivered_question(
        question_id="q", position=1, question_type=QuestionType.SCENARIO
    )
    answer = make_answer(
        question_id="q", response={"type": "SCENARIO", "text": "  I would tell my manager.  "}
    )

    view = describe_learner_answer(question, answer)

    assert view.free_text == "I would tell my manager."
    assert view.summary == "The learner wrote: I would tell my manager."


def test_an_unrecognisable_response_says_so_rather_than_guessing() -> None:
    """Reaching for the answer key to fill the gap is exactly what must not happen (§12)."""
    question = make_delivered_question(question_id="q", position=1)
    answer = LearnerAnswer(question_id="q", answered=True, response={"type": "SOMETHING_NEW"})

    view = describe_learner_answer(question, answer)

    assert view.answered is True
    assert view.summary == "The learner submitted an answer that could not be described in detail."


# ---------------------------------------------------------------------------
# Transcript mechanics (§18)
# ---------------------------------------------------------------------------


def _message(index: int, role: MessageRole = MessageRole.COACH) -> ChatMessage:
    return ChatMessage(role=role, content=f"turn {index}", index=index, created_at="t")


def test_a_transcript_counts_each_side() -> None:
    transcript = CoachingTranscript(session_id="s").appended(
        _message(0), _message(1, MessageRole.LEARNER), _message(2)
    )

    assert transcript.learner_message_count == 1
    assert transcript.coach_message_count == 2
    assert transcript.next_index == 3


def test_the_last_learner_message_is_what_a_retry_resends() -> None:
    transcript = CoachingTranscript(session_id="s").appended(
        _message(0, MessageRole.LEARNER), _message(1), _message(2, MessageRole.LEARNER)
    )

    last = transcript.last_learner_message

    assert last is not None
    assert last.index == 2


def test_a_transcript_with_no_learner_turn_has_none_to_resend() -> None:
    transcript = CoachingTranscript(session_id="s").appended(_message(0))

    assert transcript.last_learner_message is None


def test_the_replay_window_takes_the_trailing_messages() -> None:
    transcript = CoachingTranscript(session_id="s").appended(*[_message(i) for i in range(10)])

    assert [item.index for item in transcript.window(3)] == [7, 8, 9]
    assert transcript.window(0) == ()


def test_a_new_message_takes_the_next_index() -> None:
    transcript = CoachingTranscript(session_id="s").appended(_message(0))

    built = build_messages(
        transcript, role=MessageRole.LEARNER, content="hello", created_at="t"
    )

    assert built.index == 1


def test_history_is_provider_neutral() -> None:
    history = to_history([_message(0), _message(1, MessageRole.LEARNER)])

    assert history == (
        {"role": "COACH", "content": "turn 0"},
        {"role": "LEARNER", "content": "turn 1"},
    )


def test_a_transcript_serialises_its_messages() -> None:
    payload = CoachingTranscript(session_id="s").appended(_message(0)).as_dict()

    assert payload["session_id"] == "s"
    assert payload["messages"][0]["role"] == "COACH"


# ---------------------------------------------------------------------------
# Repository identity rules (§30)
# ---------------------------------------------------------------------------


def _session(session_id: str, question_id: str = "q1") -> object:
    return new_session(
        session_id=session_id,
        learner_id="l1",
        attempt_id="a1",
        course_id="c1",
        question_id=question_id,
        now="2026-02-01T08:00:00Z",
    )


async def test_a_second_session_for_the_same_question_is_refused() -> None:
    repository = InMemoryCoachingSessionRepository()
    await repository.insert(_session("s1"))

    with pytest.raises(DuplicateCoachingSessionError):
        await repository.insert(_session("s2"))


async def test_updating_an_unknown_session_is_refused() -> None:
    repository = InMemoryCoachingSessionRepository()

    with pytest.raises(CoachingSessionNotFoundError):
        await repository.update(_session("ghost"))


async def test_a_session_cannot_be_moved_to_a_different_question() -> None:
    """It would be a different conversation wearing the same id."""
    repository = InMemoryCoachingSessionRepository()
    stored = await repository.insert(_session("s1"))

    from dataclasses import replace

    with pytest.raises(DuplicateCoachingSessionError):
        await repository.update(replace(stored, question_id="q2"))


async def test_sessions_are_listed_in_delivery_order() -> None:
    from dataclasses import replace

    repository = InMemoryCoachingSessionRepository()
    await repository.insert(replace(_session("s2", "q2"), question_position=2))
    await repository.insert(replace(_session("s1", "q1"), question_position=1))

    listed = await repository.list_for_attempt("l1", "a1")

    assert [item.session_id for item in listed] == ["s1", "s2"]


async def test_a_session_is_not_readable_by_another_learner() -> None:
    repository = InMemoryCoachingSessionRepository()
    await repository.insert(_session("s1"))

    assert await repository.get_for_learner("someone-else", "s1") is None
    assert (await repository.get_for_learner("l1", "s1")).session_id == "s1"


# ---------------------------------------------------------------------------
# Enum helpers
# ---------------------------------------------------------------------------


def test_enum_parsing_is_tolerant_of_case_and_whitespace() -> None:
    assert parse_enum(CoachingSessionStatus, "  active ") is CoachingSessionStatus.ACTIVE


def test_enum_parsing_rejects_nonsense() -> None:
    assert parse_enum(CoachingSessionStatus, "ORACLE") is None
    assert parse_enum(CoachingSessionStatus, "") is None
    assert parse_enum(CoachingSessionStatus, None) is None


def test_enum_values_lists_the_vocabulary() -> None:
    assert enum_values(CoachingSessionStatus) == [
        "ACTIVE",
        "COMPLETED",
        "FAILED",
        "UNAVAILABLE",
    ]
