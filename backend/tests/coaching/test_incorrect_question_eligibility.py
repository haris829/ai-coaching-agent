"""Which questions may be coached (§9, §20, §33).

Only questions the authoritative UC-04 result marks INCORRECT. Not correct ones, not unanswered
ones, not ones from another attempt — and UC-07 has no rule of its own that could disagree with
UC-04 about which is which (§36).
"""

from __future__ import annotations

import pytest

from app.modules.coaching.domain.enums import EligibilityCode
from app.modules.coaching.domain.errors import (
    QuestionNotInAttemptError,
    QuestionNotIncorrectError,
)
from app.modules.coaching.integration.uc04 import QuestionOutcome
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
    make_result,
    make_score,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("question_id", INCORRECT_QUESTIONS)
async def test_incorrect_questions_are_eligible(world: World, question_id: str) -> None:
    world.given_standard_quiz()

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1, question_id=question_id
    )

    assert eligibility.coaching_available is True


async def test_a_correct_question_is_rejected(world: World) -> None:
    world.given_standard_quiz()

    with pytest.raises(QuestionNotIncorrectError) as error:
        await world.start(Q_SINGLE)

    assert error.value.code == "QUESTION_NOT_INCORRECT"
    # Permanent: a confirmed outcome does not change, so a client must not keep retrying.
    assert error.value.retryable is False
    assert world.llm.call_count == 0


async def test_an_unanswered_question_is_rejected_by_default(world: World) -> None:
    """§20: unanswered questions do not enter coaching unless UC-04 itself calls them incorrect."""
    world.given_standard_quiz()

    with pytest.raises(QuestionNotIncorrectError) as error:
        await world.start(Q_ORDER)

    assert error.value.context["outcome"] == QuestionOutcome.UNANSWERED.value


async def test_an_unanswered_question_is_coached_when_uc04_calls_it_incorrect(
    world: World,
) -> None:
    """The decision belongs upstream, and moving it there changes UC-07's behaviour with no code
    change here (§20, §36).
    """
    world.given_standard_quiz()
    score = world.scores.scores[ATTEMPT_1]
    world.scores.set(
        make_score(
            attempt_id=ATTEMPT_1,
            results=[
                make_result(
                    question_id=result.question_id,
                    position=result.position,
                    question_type=result.question_type,
                    outcome=(
                        QuestionOutcome.INCORRECT
                        if result.question_id == Q_ORDER
                        else result.outcome
                    ),
                    answer_key=dict(result.answer_key) if result.answer_key else None,
                )
                for result in score.question_results
            ],
        )
    )

    started = await world.start(Q_ORDER)

    assert started.coaching_available is True
    assert started.state.session.question_id == Q_ORDER


async def test_an_invalid_question_is_not_coached(world: World) -> None:
    """UC-04 could not score it, so there is no confirmed misconception to coach (§20)."""
    world.given_standard_quiz()
    world.scores.set(
        make_score(
            attempt_id=ATTEMPT_1,
            results=[
                make_result(
                    question_id=Q_MULTI,
                    position=2,
                    outcome=QuestionOutcome.INVALID,
                    awarded_marks=None,
                )
            ],
        )
    )

    with pytest.raises(QuestionNotIncorrectError):
        await world.start(Q_MULTI)


async def test_a_question_from_another_attempt_is_rejected(world: World) -> None:
    world.given_standard_quiz()
    world.given_standard_quiz(attempt_id=OTHER_ATTEMPT)
    # Blank the second attempt's results so its questions genuinely are not on it.
    world.scores.set(make_score(attempt_id=OTHER_ATTEMPT, results=[]))

    with pytest.raises(QuestionNotInAttemptError) as error:
        await world.start(Q_MULTI, attempt_id=OTHER_ATTEMPT)

    assert error.value.status_code == 404


async def test_a_question_that_does_not_exist_is_rejected(world: World) -> None:
    world.given_standard_quiz()

    with pytest.raises(QuestionNotInAttemptError):
        await world.start("q-does-not-exist")


async def test_a_scored_question_with_no_delivery_record_is_rejected(world: World) -> None:
    """UC-04 knows about it, UC-03 cannot show it. Coaching about a question nobody can display
    would be coaching about nothing.
    """
    world.given_standard_quiz()
    delivered = world.attempts.delivered[ATTEMPT_1]
    world.attempts.set_delivered(
        ATTEMPT_1, [item for item in delivered if item.question_id != Q_MULTI]
    )

    with pytest.raises(QuestionNotInAttemptError):
        await world.start(Q_MULTI)


async def test_eligibility_lists_every_question_with_its_own_flag(world: World) -> None:
    """The per-question ``coaching_available`` a frontend reads (§4, §10)."""
    world.given_standard_quiz()

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )
    by_id = {item.question_id: item for item in eligibility.questions}

    assert by_id[Q_MULTI].coaching_available is True
    assert by_id[Q_TRUE_FALSE].coaching_available is True
    assert by_id[Q_SCENARIO].coaching_available is True
    assert by_id[Q_SINGLE].coaching_available is False
    assert by_id[Q_SINGLE].reason == EligibilityCode.QUESTION_NOT_INCORRECT.value
    assert by_id[Q_ORDER].coaching_available is False
    assert eligibility.as_dict()["incorrect_question_count"] == 3


async def test_eligibility_lists_questions_in_delivery_order(world: World) -> None:
    world.given_standard_quiz()

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )

    assert [item.position for item in eligibility.questions] == [1, 2, 3, 4, 5]


async def test_no_question_list_is_produced_for_an_ineligible_attempt(world: World) -> None:
    """A refusal must not become a way to enumerate which questions someone got wrong."""
    world.given_standard_quiz()

    eligibility = await world.review.check_eligibility(
        learner_id="learner-2", attempt_id=ATTEMPT_1
    )

    assert eligibility.coaching_available is False
    assert eligibility.questions == ()
