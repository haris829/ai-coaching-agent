"""Retake eligibility and the four states (§2, §4, §13).

Two halves. The precedence between the states is a pure function and is tested as one, because
"which state wins when a learner is both out of attempts and blocked by a withdrawn quiz?" is a
business decision that should not need six fakes to assert. Then the service, where the decision is
assembled from UC-01, UC-03, the grants store and this module's reservations.
"""

from __future__ import annotations

import pytest

from app.modules.retakes.domain.allowance import compute_allowance
from app.modules.retakes.domain.eligibility import blocker, determine_state
from app.modules.retakes.domain.enums import (
    ConfigurationVersionSource,
    RetakeBlockerCode,
    RetakeState,
)
from app.modules.retakes.domain.errors import QuizNotFoundError

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# State precedence
# ---------------------------------------------------------------------------


def test_eligible_when_attempts_remain_and_nothing_blocks():
    state = determine_state(compute_allowance(maximum_attempts=3, attempts_used=1), ())
    assert state is RetakeState.ELIGIBLE


def test_additional_attempt_available_when_only_a_grant_makes_it_possible():
    allowance = compute_allowance(maximum_attempts=2, attempts_used=2, granted_attempts=1)
    assert determine_state(allowance, ()) is RetakeState.ADDITIONAL_ATTEMPT_AVAILABLE


def test_exhausted_when_the_allowance_is_the_only_problem():
    allowance = compute_allowance(maximum_attempts=2, attempts_used=2)
    blockers = (blocker(RetakeBlockerCode.NO_ATTEMPTS_REMAINING, "spent"),)
    assert determine_state(allowance, blockers) is RetakeState.EXHAUSTED


def test_unavailable_wins_over_exhausted():
    """A learner blocked by a withdrawn quiz should not be told to ask for another attempt.

    Asking would not help. The blocker list still carries both reasons, so nothing is hidden by
    the choice of headline state.
    """
    allowance = compute_allowance(maximum_attempts=2, attempts_used=2)
    blockers = (
        blocker(RetakeBlockerCode.NO_ATTEMPTS_REMAINING, "spent"),
        blocker(RetakeBlockerCode.QUIZ_NOT_AVAILABLE, "archived"),
    )
    assert determine_state(allowance, blockers) is RetakeState.UNAVAILABLE


def test_unavailable_when_an_attempt_is_still_open():
    allowance = compute_allowance(maximum_attempts=3, attempts_used=1)
    blockers = (blocker(RetakeBlockerCode.ATTEMPT_IN_PROGRESS, "open"),)
    assert determine_state(allowance, blockers) is RetakeState.UNAVAILABLE


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


async def test_eligible_learner_after_one_submitted_attempt(container, first_attempt):
    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")

    assert eligibility.state is RetakeState.ELIGIBLE
    assert eligibility.can_retake is True
    assert eligibility.allowance.maximum_attempts == 2
    assert eligibility.allowance.attempts_used == 1
    assert eligibility.allowance.available_attempts == 1
    assert eligibility.previous_attempt_id == first_attempt.attempt_id
    assert eligibility.next_attempt_number == 2
    assert eligibility.guidance is None


async def test_exhausted_learner_gets_administrator_guidance(
    container, quiz, attempts, settings
):
    """§13: the backend, not a disabled button, is what refuses — and it explains itself."""
    for questions in (("q1", "q2", "q3"), ("q4", "q5", "q6")):
        attempt = attempts.start_attempt(question_ids=questions)
        attempts.submit(attempt.attempt_id)

    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")

    assert eligibility.state is RetakeState.EXHAUSTED
    assert eligibility.can_retake is False
    assert eligibility.allowance.available_attempts == 0
    assert eligibility.guidance == settings.exhausted_contact_guidance
    assert [item.code for item in eligibility.blockers] == [
        RetakeBlockerCode.NO_ATTEMPTS_REMAINING
    ]


async def test_grant_makes_an_exhausted_learner_eligible_again(container, quiz, attempts, admin_headers):
    for questions in (("q1", "q2", "q3"), ("q4", "q5", "q6")):
        attempt = attempts.start_attempt(question_ids=questions)
        attempts.submit(attempt.attempt_id)

    await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-4471",
    )

    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")
    assert eligibility.state is RetakeState.ADDITIONAL_ATTEMPT_AVAILABLE
    assert eligibility.can_retake is True
    assert eligibility.allowance.granted_attempts == 1
    assert eligibility.allowance.total_entitlement == 3
    # The course-wide maximum is untouched — that is the whole point of §11.
    assert eligibility.allowance.maximum_attempts == 2


async def test_no_completed_attempt_is_not_a_retake(container, quiz):
    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")
    assert eligibility.state is RetakeState.UNAVAILABLE
    assert RetakeBlockerCode.NO_COMPLETED_ATTEMPT in {
        item.code for item in eligibility.blockers
    }


async def test_an_attempt_still_in_progress_blocks_a_retake(container, quiz, attempts):
    attempts.start_attempt(question_ids=("q1", "q2", "q3"))  # left ACTIVE
    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")

    assert eligibility.state is RetakeState.UNAVAILABLE
    codes = {item.code for item in eligibility.blockers}
    assert RetakeBlockerCode.ATTEMPT_IN_PROGRESS in codes


async def test_submission_pending_is_not_retakeable(container, quiz, attempts):
    """A committed but not-yet-finalised attempt is still open, so it cannot be retaken yet."""
    from app.modules.retakes.integration.uc03 import AttemptStatus

    attempts.start_attempt(
        question_ids=("q1", "q2", "q3"), status=AttemptStatus.SUBMISSION_PENDING
    )
    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")

    assert eligibility.state is RetakeState.UNAVAILABLE
    assert RetakeBlockerCode.ATTEMPT_IN_PROGRESS in {item.code for item in eligibility.blockers}


async def test_withdrawn_quiz_blocks_a_retake(container, first_attempt, configurations):
    configurations.withdraw_quiz()
    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")

    assert eligibility.state is RetakeState.UNAVAILABLE
    assert RetakeBlockerCode.QUIZ_NOT_AVAILABLE in {item.code for item in eligibility.blockers}


async def test_unknown_quiz_is_a_not_found(container):
    with pytest.raises(QuizNotFoundError):
        await container.services.eligibility.check("learner-alice", "quiz-nope")


# ---------------------------------------------------------------------------
# Which configuration version a retake would lock (§4)
# ---------------------------------------------------------------------------


async def test_version_is_carried_forward_when_nothing_has_been_published(
    container, first_attempt
):
    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")
    assert eligibility.configuration_version_id == "cfg-v1"
    assert eligibility.configuration_version_source is ConfigurationVersionSource.CARRIED_FORWARD


async def test_version_advances_when_uc01_has_published_a_newer_one(
    container, first_attempt, configurations
):
    """UC-03's rule for any new attempt: lock the version active at creation.

    The previous attempt keeps cfg-v1; the retake would lock cfg-v2. Both are recorded, so the
    change is visible rather than silent.
    """
    configurations.publish(configuration_version_id="cfg-v2", version=2, maximum_attempts=2)

    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")
    assert eligibility.configuration_version_id == "cfg-v2"
    assert (
        eligibility.configuration_version_source is ConfigurationVersionSource.ADVANCED_TO_ACTIVE
    )
    # The historical attempt is untouched.
    assert first_attempt.configuration_version_id == "cfg-v1"


async def test_carry_forward_policy_pins_the_retake_to_the_previous_version(
    settings, clock, configurations, bank, attempts, scores, results, feedback, coaching, audit
):
    from app.modules.retakes.container import create_container
    from tests.retakes.world import SequentialIdGenerator

    pinned = settings.model_copy(update={"retake_configuration_policy": "CARRY_FORWARD_PREVIOUS"})
    configurations.publish(question_count=3, maximum_attempts=3)
    bank.add_many(10)
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)
    configurations.publish(configuration_version_id="cfg-v2", version=2, maximum_attempts=3)

    container = create_container(
        settings=pinned,
        clock=clock,
        new_id=SequentialIdGenerator("uc08"),
        configurations=configurations,
        question_bank=bank,
        attempts=attempts,
        scores=scores,
        results=results,
        feedback=feedback,
        coaching=coaching,
        audit=audit,
    )
    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")

    assert eligibility.configuration_version_id == "cfg-v1"
    assert eligibility.configuration_version_source is ConfigurationVersionSource.PINNED_TO_PREVIOUS


async def test_no_active_version_blocks_a_retake(container, first_attempt, configurations):
    configurations.deactivate()
    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")

    assert eligibility.state is RetakeState.UNAVAILABLE
    assert RetakeBlockerCode.CONFIGURATION_UNAVAILABLE in {
        item.code for item in eligibility.blockers
    }
    assert eligibility.configuration_version_id is None


async def test_an_active_version_for_another_quiz_is_refused(
    container, first_attempt, configurations
):
    """The guard that makes an accidental configuration switch impossible, not merely unlikely."""
    stray = configurations.publish(
        configuration_version_id="cfg-other",
        version=9,
        quiz_id="quiz-other",
        course_id="course-other",
        activate=False,
    )
    configurations.active["quiz-1"] = stray.configuration_version_id

    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")
    assert eligibility.can_retake is False
    assert RetakeBlockerCode.CONFIGURATION_UNAVAILABLE in {
        item.code for item in eligibility.blockers
    }


async def test_the_maximum_comes_from_the_locked_version_not_todays(
    container, quiz, attempts, configurations
):
    """UC-05's rule, adopted: a limit lowered afterwards cannot strip an attempt already held."""
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)
    # An administrator publishes a stricter version: one attempt only.
    configurations.publish(configuration_version_id="cfg-v2", version=2, maximum_attempts=1)

    eligibility = await container.services.eligibility.check("learner-alice", "quiz-1")

    # The learner still holds the second attempt cfg-v1 granted them.
    assert eligibility.allowance.maximum_attempts == 2
    assert eligibility.can_retake is True
    # And the retake would run under the new active version.
    assert eligibility.configuration_version_id == "cfg-v2"
