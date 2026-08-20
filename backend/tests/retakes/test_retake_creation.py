"""Creating a retake (§3, §4, §5, §6, §7, §8, §10, §13, §17).

The properties asserted here are the ones the module exists to guarantee: the new attempt is
independent, the previous attempt is untouched, the configuration version is the right one, the
paper is genuinely different, and every refusal happens before anything is written.
"""

from __future__ import annotations

import pytest

from app.modules.retakes.domain.enums import (
    ConfigurationVersionSource,
    ExclusionScope,
    RetakeAnomalyCode,
    RetakeRequestStatus,
)
from app.modules.retakes.domain.errors import (
    AttemptInProgressError,
    AttemptNotFoundError,
    AttemptOwnershipError,
    InsufficientQuestionsError,
    NoAttemptsRemainingError,
    NoCompletedAttemptError,
    PreviousAttemptNotRetakeableError,
    PreviousAttemptQuizMismatchError,
    PreviousAttemptSupersededError,
    QuestionBankUnavailableError,
    QuizNotAvailableError,
)
from app.modules.retakes.integration.uc03 import AttemptStatus

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# The independent attempt (§3, §10)
# ---------------------------------------------------------------------------


async def test_retake_creates_a_new_independent_attempt(container, first_attempt, attempts):
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert outcome.replayed is False
    assert outcome.attempt is not None
    assert outcome.attempt.attempt_id != first_attempt.attempt_id
    assert outcome.attempt.attempt_number == 2
    assert outcome.attempt.status is AttemptStatus.ACTIVE
    assert outcome.attempt.learner_id == "learner-alice"
    assert outcome.attempt.course_id == "course-1"
    assert outcome.attempt.quiz_id == "quiz-1"
    # Two attempts now exist; neither replaced the other.
    assert len(await attempts.list_attempts("learner-alice", "quiz-1")) == 2


async def test_the_previous_attempt_is_completely_unchanged(container, first_attempt, attempts):
    """§3, asserted by comparing a snapshot of every stored attempt before and after."""
    before = attempts.snapshot()[first_attempt.attempt_id]

    await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    after = attempts.snapshot()[first_attempt.attempt_id]
    assert after == before
    assert after["context"].status is AttemptStatus.SUBMITTED
    assert after["context"].submitted_at == "2026-01-01T09:30:00.000Z"
    assert after["question_ids"] == ("q1", "q2", "q3")
    assert after["context"].configuration_version_id == "cfg-v1"
    assert after["context"].attempt_number == 1


async def test_the_retake_records_its_lineage(container, first_attempt):
    """§10: the relationship, stored on the retake rather than in a new structure."""
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert outcome.retake.previous_attempt_id == first_attempt.attempt_id
    assert outcome.retake.attempt_id == outcome.attempt.attempt_id
    assert outcome.retake.status is RetakeRequestStatus.COMPLETED
    assert outcome.retake.attempt_number == 2
    assert outcome.retake.completed_at == "2026-01-02T10:00:00Z"


async def test_the_retake_is_audited(container, first_attempt, audit):
    await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")
    assert "retake_created" in audit.codes()


# ---------------------------------------------------------------------------
# Configuration version (§4)
# ---------------------------------------------------------------------------


async def test_the_retake_locks_the_applicable_version(container, first_attempt):
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert outcome.attempt.configuration_version_id == "cfg-v1"
    assert outcome.retake.configuration_version_id == "cfg-v1"
    assert (
        outcome.retake.configuration_version_source is ConfigurationVersionSource.CARRIED_FORWARD
    )


async def test_a_newer_published_version_is_locked_and_recorded(
    container, first_attempt, configurations, attempts
):
    configurations.publish(
        configuration_version_id="cfg-v2", version=2, maximum_attempts=3, question_count=3
    )

    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert outcome.attempt.configuration_version_id == "cfg-v2"
    assert (
        outcome.retake.configuration_version_source
        is ConfigurationVersionSource.ADVANCED_TO_ACTIVE
    )
    # The historical attempt still points at the version it ran under.
    stored = await attempts.get_attempt(first_attempt.attempt_id)
    assert stored.configuration_version_id == "cfg-v1"


# ---------------------------------------------------------------------------
# The question set (§5, §6, §7)
# ---------------------------------------------------------------------------


async def test_the_retake_delivers_a_wholly_fresh_paper(container, first_attempt):
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    delivered = set(outcome.attempt.delivered_question_ids)
    assert len(delivered) == 3
    assert delivered.isdisjoint({"q1", "q2", "q3"})
    assert outcome.difference.new_question_count == 3
    assert outcome.difference.identical_question_set is False
    assert outcome.difference.satisfied is True
    assert outcome.retake.anomalies == ()


async def test_the_plan_is_recorded_on_the_retake(container, first_attempt):
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    plan = outcome.retake.question_plan
    assert plan["exclusion_scope"] == ExclusionScope.ALL_PREVIOUS_ATTEMPTS.value
    assert plan["required_count"] == 3
    assert plan["eligible_pool_size"] == 10
    assert plan["unused_pool_size"] == 7
    assert plan["reuse_expected"] is False


async def test_previously_used_questions_are_passed_to_the_selector(
    container, first_attempt, attempts
):
    """UC-08's contribution to selection is the exclusion set, and nothing else (§6)."""
    await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    request = attempts.created_requests[-1]
    assert set(request.deprioritised_question_ids) == {"q1", "q2", "q3"}
    assert request.configuration_version_id == "cfg-v1"
    assert request.attempt_number == 2
    assert request.retake_of_attempt_id == first_attempt.attempt_id


async def test_retired_questions_are_never_delivered_to_avoid_reuse(
    container, quiz, attempts, bank
):
    """§8: reuse is preferable to a withdrawn question."""
    for question_id in ("q4", "q5", "q6", "q7", "q8", "q9", "q10"):
        bank.retire(question_id)
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)

    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    delivered = set(outcome.attempt.delivered_question_ids)
    assert delivered == {"q1", "q2", "q3"}
    assert outcome.difference.reuse_unavoidable is True
    # Recorded, not silent — and not treated as a defect, because nothing better was possible.
    codes = {item.code for item in outcome.retake.anomalies}
    assert RetakeAnomalyCode.QUESTION_REUSE_UNAVOIDABLE in codes
    assert RetakeAnomalyCode.QUESTION_SET_NOT_MEANINGFULLY_DIFFERENT not in codes


async def test_reuse_is_recorded_when_the_bank_is_too_small(container, configurations, attempts, bank):
    """§8's scenario end to end: configured 10, previously used 8, five alternatives."""
    configurations.publish(question_count=10, maximum_attempts=3)
    bank.add_many(13)
    previous = tuple(f"q{index}" for index in range(1, 9))
    attempt = attempts.start_attempt(question_ids=previous)
    attempts.submit(attempt.attempt_id)

    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert len(outcome.attempt.delivered_question_ids) == 10
    assert outcome.plan.exclusion_scope is ExclusionScope.NONE
    assert outcome.plan.reuse_expected is True
    assert outcome.difference.new_question_count == 5
    assert outcome.difference.repeated_question_count == 5
    # Satisfied: five new questions was the most the bank could supply.
    assert outcome.difference.satisfied is True
    assert RetakeAnomalyCode.QUESTION_REUSE_UNAVOIDABLE in {
        item.code for item in outcome.retake.anomalies
    }


async def test_a_selector_that_ignores_the_exclusion_is_reported(
    container, first_attempt, attempts
):
    """§7's validation rule, doing its job.

    The attempt is not destroyed — it exists and the learner can sit it — but the finding is
    recorded and returned rather than passing unnoticed.
    """
    attempts.ignore_exclusions = True

    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert set(outcome.attempt.delivered_question_ids) == {"q1", "q2", "q3"}
    assert outcome.difference.identical_question_set is True
    assert outcome.difference.satisfied is False
    assert RetakeAnomalyCode.QUESTION_SET_NOT_MEANINGFULLY_DIFFERENT in {
        item.code for item in outcome.retake.anomalies
    }
    # And the retake still completed: the attempt is real.
    assert outcome.retake.status is RetakeRequestStatus.COMPLETED


async def test_a_third_attempt_avoids_both_earlier_papers(
    container, configurations, attempts, bank
):
    """The whole history is the preferred exclusion set, not just the last paper (§5)."""
    configurations.publish(question_count=3, maximum_attempts=4)
    bank.add_many(12)
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)

    second = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )
    attempts.submit(second.attempt.attempt_id)
    third = await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    seen = {"q1", "q2", "q3"} | set(second.attempt.delivered_question_ids)
    assert set(third.attempt.delivered_question_ids).isdisjoint(seen)
    assert third.plan.exclusion_scope is ExclusionScope.ALL_PREVIOUS_ATTEMPTS


# ---------------------------------------------------------------------------
# Refusals — every one of them before anything is written (§13, §17)
# ---------------------------------------------------------------------------


async def test_exhausted_learner_is_refused_by_the_backend(container, quiz, attempts, settings):
    for questions in (("q1", "q2", "q3"), ("q4", "q5", "q6")):
        attempt = attempts.start_attempt(question_ids=questions)
        attempts.submit(attempt.attempt_id)
    before = attempts.snapshot()

    with pytest.raises(NoAttemptsRemainingError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert raised.value.code == "MAX_ATTEMPTS_REACHED"
    assert raised.value.context["available_attempts"] == 0
    assert raised.value.context["guidance"] == settings.exhausted_contact_guidance
    # Existing attempts unchanged, and nothing new created.
    assert attempts.snapshot() == before


async def test_a_learner_with_no_completed_attempt_is_refused(container, quiz):
    with pytest.raises(NoCompletedAttemptError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")


async def test_an_open_attempt_blocks_a_retake(container, quiz, attempts):
    attempts.start_attempt(question_ids=("q1", "q2", "q3"))  # left ACTIVE
    with pytest.raises(PreviousAttemptNotRetakeableError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")


async def test_an_open_attempt_blocks_a_named_previous_attempt_too(
    container, quiz, attempts
):
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)
    attempts.start_attempt(question_ids=("q4", "q5", "q6"))  # a second, still open

    with pytest.raises(AttemptInProgressError):
        await container.services.retakes.create(
            learner_id="learner-alice",
            quiz_id="quiz-1",
            previous_attempt_id=first.attempt_id,
        )


async def test_a_withdrawn_quiz_refuses_a_retake(container, first_attempt, configurations):
    configurations.withdraw_quiz(reason="ARCHIVED")
    with pytest.raises(QuizNotAvailableError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")
    assert raised.value.context["reason"] == "ARCHIVED"


async def test_an_unknown_previous_attempt_is_a_not_found(container, first_attempt):
    with pytest.raises(AttemptNotFoundError):
        await container.services.retakes.create(
            learner_id="learner-alice", quiz_id="quiz-1", previous_attempt_id="attempt-nope"
        )


async def test_another_learners_attempt_is_refused_as_forbidden(container, quiz, attempts):
    other = attempts.start_attempt(learner_id="learner-bob", question_ids=("q1", "q2", "q3"))
    attempts.submit(other.attempt_id)

    with pytest.raises(AttemptOwnershipError):
        await container.services.retakes.create(
            learner_id="learner-alice", quiz_id="quiz-1", previous_attempt_id=other.attempt_id
        )


async def test_an_attempt_at_another_quiz_is_refused(container, configurations, attempts, bank):
    configurations.publish(question_count=3, maximum_attempts=2)
    configurations.publish(
        configuration_version_id="cfg-other", quiz_id="quiz-2", course_id="course-1"
    )
    bank.add_many(6)
    other = attempts.start_attempt(
        quiz_id="quiz-2", configuration_version_id="cfg-other", question_ids=("q1", "q2", "q3")
    )
    attempts.submit(other.attempt_id)

    with pytest.raises(PreviousAttemptQuizMismatchError):
        await container.services.retakes.create(
            learner_id="learner-alice", quiz_id="quiz-1", previous_attempt_id=other.attempt_id
        )


async def test_a_superseded_previous_attempt_is_refused(container, configurations, attempts, bank):
    """Only the latest completed attempt can be retaken."""
    configurations.publish(question_count=3, maximum_attempts=4)
    bank.add_many(12)
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)
    second = attempts.start_attempt(question_ids=("q4", "q5", "q6"))
    attempts.submit(second.attempt_id)

    with pytest.raises(PreviousAttemptSupersededError):
        await container.services.retakes.create(
            learner_id="learner-alice", quiz_id="quiz-1", previous_attempt_id=first.attempt_id
        )


async def test_an_undersized_bank_refuses_before_reserving(container, configurations, attempts, bank):
    configurations.publish(question_count=8, maximum_attempts=3)
    bank.add_many(5)
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)

    with pytest.raises(InsufficientQuestionsError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert raised.value.context["required_count"] == 8
    # Nothing was reserved, so the learner has not lost an attempt to a configuration problem.
    assert await container.repositories.retakes.count_active_reservations(
        "learner-alice", "quiz-1"
    ) == 0


async def test_an_unavailable_question_bank_refuses_before_reserving(
    container, first_attempt, bank
):
    bank.failure = QuestionBankUnavailableError()

    with pytest.raises(QuestionBankUnavailableError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert await container.repositories.retakes.count_active_reservations(
        "learner-alice", "quiz-1"
    ) == 0
