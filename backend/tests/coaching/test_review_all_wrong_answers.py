"""Review all wrong answers (§19, §20, §30, §33).

    submitted attempt → find every incorrect question → order them → coach through them in turn

The queue is derived from UC-04's outcomes and the coaching sessions that exist, so these tests also
cover what happens when a learner abandons a review halfway through and comes back.
"""

from __future__ import annotations

import pytest

from app.modules.coaching.domain.enums import ReviewItemStatus, SessionOutcome
from app.modules.coaching.domain.errors import NoIncorrectQuestionsError
from tests.coaching.world import (
    ATTEMPT_1,
    INCORRECT_QUESTIONS,
    LEARNER,
    OTHER_ATTEMPT,
    Q_MULTI,
    Q_ORDER,
    Q_SCENARIO,
    Q_SINGLE,
    Q_TRUE_FALSE,
    World,
    make_score,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# What is in the queue (§20)
# ---------------------------------------------------------------------------


async def test_all_incorrect_questions_are_discovered(world: World) -> None:
    world.given_standard_quiz()

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert tuple(item.question_id for item in queue.items) == INCORRECT_QUESTIONS
    assert queue.total == 3


async def test_correct_and_unanswered_questions_are_excluded(world: World) -> None:
    world.given_standard_quiz()

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)
    ids = {item.question_id for item in queue.items}

    assert Q_SINGLE not in ids
    assert Q_ORDER not in ids


async def test_questions_are_ordered_by_delivery_position(world: World) -> None:
    world.given_standard_quiz()

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert [item.position for item in queue.items] == [2, 3, 5]


async def test_each_item_carries_its_topic(world: World) -> None:
    world.given_standard_quiz()

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert [item.topic for item in queue.items] == [
        "Reporting concerns",
        "Confidentiality",
        "Escalation",
    ]


async def test_another_learners_review_is_refused(world: World) -> None:
    world.given_standard_quiz()

    with pytest.raises(Exception) as error:
        await world.review.get_review(learner_id="learner-2", attempt_id=ATTEMPT_1)

    assert getattr(error.value, "status_code", None) == 403


async def test_sessions_from_another_attempt_do_not_affect_the_queue(world: World) -> None:
    world.given_standard_quiz()
    world.given_standard_quiz(attempt_id=OTHER_ATTEMPT)
    await world.start(Q_MULTI, attempt_id=OTHER_ATTEMPT)

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert all(item.status is ReviewItemStatus.PENDING for item in queue.items)


# ---------------------------------------------------------------------------
# Working through it (§19)
# ---------------------------------------------------------------------------


async def test_the_first_question_is_offered_first(world: World) -> None:
    world.given_standard_quiz()

    advance = await world.review.next_question(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert advance.next_item is not None
    assert advance.next_item.question_id == Q_MULTI


async def test_starting_a_question_marks_it_in_progress(world: World) -> None:
    world.given_standard_quiz()
    await world.start(Q_MULTI)

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert queue.item_for(Q_MULTI).status is ReviewItemStatus.IN_PROGRESS
    assert queue.item_for(Q_MULTI).session_id == "session-0001"


async def test_an_unfinished_question_is_returned_again_rather_than_skipped(
    world: World,
) -> None:
    """A learner who steps away comes back to the conversation they were having (§19)."""
    world.given_standard_quiz()
    await world.start(Q_MULTI)

    advance = await world.review.next_question(
        learner_id=LEARNER, attempt_id=ATTEMPT_1, complete_current=False
    )

    assert advance.next_item.question_id == Q_MULTI
    assert advance.completed_question_id is None


async def test_completing_a_question_advances_to_the_next(world: World) -> None:
    world.given_standard_quiz()
    await world.start(Q_MULTI)

    advance = await world.review.next_question(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert advance.completed_question_id == Q_MULTI
    assert advance.next_item.question_id == Q_TRUE_FALSE
    assert advance.queue.completed_count == 1


async def test_the_learner_walks_the_whole_queue_in_order(world: World) -> None:
    """Question 1 → 2 → 3, then finished (§19)."""
    world.given_standard_quiz()
    visited: list[str] = []

    for _ in INCORRECT_QUESTIONS:
        advance = await world.review.next_question(
            learner_id=LEARNER, attempt_id=ATTEMPT_1
        )
        question_id = advance.next_item.question_id
        visited.append(question_id)
        await world.start(question_id)

    final = await world.review.next_question(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert visited == list(INCORRECT_QUESTIONS)
    assert final.next_item is None
    assert final.queue.finished is True
    assert final.queue.remaining_count == 0


async def test_advancing_past_the_end_is_idempotent(world: World) -> None:
    world.given_standard_quiz()
    for question_id in INCORRECT_QUESTIONS:
        await world.start(question_id)
        await world.coaching.complete_session(
            learner_id=LEARNER,
            session_id=(
                await world.sessions.find_open(LEARNER, ATTEMPT_1, question_id)
            ).session_id,
        )

    first = await world.review.next_question(learner_id=LEARNER, attempt_id=ATTEMPT_1)
    second = await world.review.next_question(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert first.as_dict() == second.as_dict()
    assert second.next_item is None


async def test_completing_a_session_is_idempotent(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    first = await world.coaching.complete_session(learner_id=LEARNER, session_id=session_id)
    second = await world.coaching.complete_session(learner_id=LEARNER, session_id=session_id)

    assert first.session.completed_at == second.session.completed_at
    assert first.session.revision == second.session.revision


async def test_a_learner_can_return_to_a_finished_question(world: World) -> None:
    """Reopening the same session, never a second one (§30)."""
    world.given_standard_quiz()
    started = await world.start(Q_MULTI)
    session_id = started.state.session.session_id
    await world.say(session_id, "I think I see it now.")
    await world.coaching.complete_session(learner_id=LEARNER, session_id=session_id)

    resumed = await world.start(Q_MULTI)

    assert resumed.outcome is SessionOutcome.RESUMED
    assert resumed.state.session.session_id == session_id
    assert resumed.state.session.completed_at is None
    assert len(resumed.state.transcript.messages) == 3


async def test_an_attempt_with_nothing_wrong_has_nothing_to_review(world: World) -> None:
    world.given_standard_quiz()
    world.scores.set(make_score(attempt_id=ATTEMPT_1, results=[]))

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)
    with pytest.raises(NoIncorrectQuestionsError) as error:
        await world.review.next_question(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert queue.total == 0
    assert error.value.code == "NO_INCORRECT_QUESTIONS"


async def test_the_queue_reports_progress(world: World) -> None:
    world.given_standard_quiz()
    await world.start(Q_MULTI)
    await world.review.next_question(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    payload = (
        await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)
    ).as_dict()

    assert payload["total_incorrect"] == 3
    assert payload["completed_count"] == 1
    assert payload["remaining_count"] == 2
    assert payload["next_question_id"] == Q_TRUE_FALSE


async def test_every_unfinished_item_reports_coaching_available(world: World) -> None:
    """The per-item flag a frontend reads (§4, §10)."""
    world.given_standard_quiz()
    await world.start(Q_MULTI)
    await world.review.next_question(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)
    flags = {item.question_id: item.as_dict()["coaching_available"] for item in queue.items}

    assert flags == {Q_MULTI: False, Q_TRUE_FALSE: True, Q_SCENARIO: True}


async def test_the_review_is_readable_while_the_ai_is_down(world: World) -> None:
    """Seeing which questions you got wrong does not require a model (§27)."""
    world.given_standard_quiz()
    world.llm.available = False

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert queue.total == 3


async def test_a_stalled_session_keeps_its_place_in_the_queue(world: World) -> None:
    """An AI failure must not silently skip the learner past the question (§28)."""
    world.given_standard_quiz()
    world.llm.go_offline()
    await world.start(Q_MULTI)

    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert queue.item_for(Q_MULTI).status is ReviewItemStatus.IN_PROGRESS
    assert queue.next_item().question_id == Q_MULTI
