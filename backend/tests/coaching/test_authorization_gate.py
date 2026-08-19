"""The post-submission coaching gate (§7, §8, §9, §33).

    attempt submitted  AND  feedback available  →  coaching
    anything else                               →  denied

These are the tests that make §8's "do not rely only on frontend hiding" true. Every denial below
is produced by the domain, with no HTTP layer involved.
"""

from __future__ import annotations

import pytest

from app.modules.coaching.domain.enums import EligibilityCode
from app.modules.coaching.domain.errors import (
    AttemptNotFoundError,
    AttemptNotSubmittedError,
    CoachingServiceUnavailableError,
    FeedbackUnavailableError,
    LearnerNotAuthorizedError,
    ScoreNotConfirmedError,
)
from app.modules.coaching.integration.uc03 import AttemptStatus
from app.modules.coaching.integration.uc04 import ScoreStatus
from app.modules.coaching.integration.uc06 import FeedbackStatus
from tests.coaching.world import ATTEMPT_1, LEARNER, OTHER_LEARNER, Q_MULTI, World

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Submitted + feedback available → allowed
# ---------------------------------------------------------------------------


async def test_submitted_attempt_with_available_feedback_is_eligible(world: World) -> None:
    world.given_standard_quiz()

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )

    assert eligibility.coaching_available is True
    assert eligibility.eligibility.code is EligibilityCode.ELIGIBLE


async def test_submitted_attempt_can_start_coaching(world: World) -> None:
    world.given_standard_quiz()

    started = await world.start(Q_MULTI)

    assert started.coaching_available is True
    assert started.state.session.attempt_id == ATTEMPT_1


# ---------------------------------------------------------------------------
# Active quiz protection (§8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        AttemptStatus.NOT_STARTED,
        AttemptStatus.ACTIVE,
        AttemptStatus.SUBMISSION_PENDING,
        AttemptStatus.ABANDONED,
    ],
)
async def test_unsubmitted_attempt_is_denied(world: World, status: AttemptStatus) -> None:
    world.given_standard_quiz(attempt_status=status)

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )

    assert eligibility.coaching_available is False
    assert eligibility.eligibility.code is EligibilityCode.ATTEMPT_NOT_SUBMITTED


async def test_starting_coaching_during_an_active_attempt_raises(world: World) -> None:
    world.given_standard_quiz(attempt_status=AttemptStatus.ACTIVE)

    with pytest.raises(AttemptNotSubmittedError) as error:
        await world.start(Q_MULTI)

    assert error.value.status_code == 409
    assert error.value.code == "ATTEMPT_NOT_SUBMITTED"
    # The AI was never asked anything about an in-progress quiz.
    assert world.llm.call_count == 0


async def test_active_attempt_denial_does_not_depend_on_the_ai_being_up(world: World) -> None:
    """The gate is a domain rule, not a side effect of the model being unreachable (§8)."""
    world.given_standard_quiz(attempt_status=AttemptStatus.ACTIVE)
    world.llm.available = True

    with pytest.raises(AttemptNotSubmittedError):
        await world.start(Q_MULTI)


# ---------------------------------------------------------------------------
# Feedback availability (§7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [FeedbackStatus.PENDING, FeedbackStatus.FAILED, FeedbackStatus.NOT_FOUND],
)
async def test_unavailable_feedback_denies_coaching(
    world: World, status: FeedbackStatus
) -> None:
    world.given_standard_quiz(feedback_status=status)

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )

    assert eligibility.coaching_available is False
    assert eligibility.eligibility.code is EligibilityCode.FEEDBACK_UNAVAILABLE


async def test_starting_coaching_without_feedback_raises(world: World) -> None:
    world.given_standard_quiz(feedback_status=FeedbackStatus.PENDING)

    with pytest.raises(FeedbackUnavailableError) as error:
        await world.start(Q_MULTI)

    assert error.value.code == "FEEDBACK_UNAVAILABLE"
    assert error.value.retryable is True
    assert world.llm.call_count == 0


async def test_missing_feedback_record_denies_coaching(world: World) -> None:
    world.given_standard_quiz()
    world.feedback.records.clear()

    with pytest.raises(FeedbackUnavailableError):
        await world.start(Q_MULTI)


# ---------------------------------------------------------------------------
# Scoring (§9)
# ---------------------------------------------------------------------------


async def test_pending_score_denies_coaching(world: World) -> None:
    world.given_standard_quiz(score_status=ScoreStatus.PENDING)

    with pytest.raises(ScoreNotConfirmedError) as error:
        await world.start(Q_MULTI)

    assert error.value.code == "SCORE_NOT_CONFIRMED"


async def test_missing_score_denies_coaching(world: World) -> None:
    world.given_standard_quiz()
    world.scores.scores.clear()

    with pytest.raises(ScoreNotConfirmedError):
        await world.start(Q_MULTI)


# ---------------------------------------------------------------------------
# Existence and ownership (§9)
# ---------------------------------------------------------------------------


async def test_unknown_attempt_is_not_found(world: World) -> None:
    with pytest.raises(AttemptNotFoundError):
        await world.start(Q_MULTI, attempt_id="no-such-attempt")


async def test_another_learners_attempt_is_forbidden(world: World) -> None:
    world.given_standard_quiz()

    with pytest.raises(LearnerNotAuthorizedError) as error:
        await world.start(Q_MULTI, learner_id=OTHER_LEARNER)

    assert error.value.status_code == 403


async def test_ownership_failure_reveals_nothing_about_the_attempt(world: World) -> None:
    """Probing someone else's attempt must not leak its state (§9).

    An unsubmitted attempt and a submitted one belonging to another learner must produce the same
    refusal, or the endpoint becomes a way to watch a classmate's progress.
    """
    world.given_standard_quiz(attempt_status=AttemptStatus.ACTIVE)
    active = await world.review.check_eligibility(
        learner_id=OTHER_LEARNER, attempt_id=ATTEMPT_1
    )

    world.given_standard_quiz(attempt_status=AttemptStatus.SUBMITTED)
    submitted = await world.review.check_eligibility(
        learner_id=OTHER_LEARNER, attempt_id=ATTEMPT_1
    )

    assert active.eligibility.code is EligibilityCode.NOT_ATTEMPT_OWNER
    assert active.as_dict() == submitted.as_dict()


# ---------------------------------------------------------------------------
# Service availability (§9's seventh check, §27)
# ---------------------------------------------------------------------------


async def test_unavailable_coaching_service_denies_a_start(world: World) -> None:
    world.given_standard_quiz()
    world.llm.available = False

    with pytest.raises(CoachingServiceUnavailableError) as error:
        await world.start(Q_MULTI)

    assert error.value.status_code == 503
    assert error.value.retryable is True
    # Nothing was created, so there is no orphan session to clean up.
    assert await world.sessions.find_open(LEARNER, ATTEMPT_1, Q_MULTI) is None


async def test_a_broken_availability_probe_counts_as_unavailable(world: World) -> None:
    """A probe that raises is not evidence the service is healthy (§27)."""
    world.given_standard_quiz()
    world.llm.availability_raises = True

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )

    assert eligibility.eligibility.code is EligibilityCode.SERVICE_UNAVAILABLE
    assert eligibility.eligibility.retryable is True


async def test_permanent_refusals_outrank_a_temporary_outage(world: World) -> None:
    """A correctly answered question is refused as such even while the AI is down (§9)."""
    world.given_standard_quiz()
    world.llm.available = False

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1, question_id="q-single"
    )

    assert eligibility.eligibility.code is EligibilityCode.QUESTION_NOT_INCORRECT
    assert eligibility.eligibility.retryable is False


# ---------------------------------------------------------------------------
# The gate runs on every operation, not only at the start (§8)
# ---------------------------------------------------------------------------


async def test_feedback_withdrawn_mid_session_stops_further_coaching(world: World) -> None:
    world.given_standard_quiz()
    started = await world.start(Q_MULTI)

    world.given_standard_quiz(feedback_status=FeedbackStatus.PENDING)

    with pytest.raises(FeedbackUnavailableError):
        await world.say(started.state.session.session_id, "Why was that wrong?")


async def test_reopened_attempt_stops_further_coaching(world: World) -> None:
    world.given_standard_quiz()
    started = await world.start(Q_MULTI)

    world.given_standard_quiz(attempt_status=AttemptStatus.ACTIVE)

    with pytest.raises(AttemptNotSubmittedError):
        await world.say(started.state.session.session_id, "Tell me more.")
