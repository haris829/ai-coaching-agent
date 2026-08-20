"""The allowance arithmetic (§1).

    configured maximum − attempts used + granted attempts = available attempts

Pure-function tests first, because everything else in the module trusts this calculation. Then the
service, where the used count and the grants come from three different places and the interesting
question is whether an in-flight reservation is counted.
"""

from __future__ import annotations

import pytest

from app.core.time import to_iso
from app.modules.retakes.domain.allowance import compute_allowance, is_valid_maximum
from app.modules.retakes.domain.enums import (
    ConfigurationVersionSource,
    GrantStatus,
    RetakeRequestStatus,
)
from app.modules.retakes.domain.grants import AdditionalAttemptGrant, total_granted_attempts
from app.modules.retakes.domain.requests import RetakeRequest

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# The pure calculation
# ---------------------------------------------------------------------------


def test_available_attempts_is_maximum_minus_used():
    allowance = compute_allowance(maximum_attempts=3, attempts_used=1)
    assert allowance.available_attempts == 2
    assert allowance.has_available_attempts is True
    assert allowance.relies_on_grant is False


def test_grant_adds_to_the_entitlement_without_touching_the_maximum():
    allowance = compute_allowance(maximum_attempts=2, attempts_used=2, granted_attempts=1)
    # The §11 example: global maximum stays 2, this learner's entitlement is 3.
    assert allowance.maximum_attempts == 2
    assert allowance.granted_attempts == 1
    assert allowance.total_entitlement == 3
    assert allowance.available_attempts == 1
    # The learner would have nothing left without the grant, which is what lets the caller report
    # ADDITIONAL_ATTEMPT_AVAILABLE rather than a plain ELIGIBLE.
    assert allowance.relies_on_grant is True


def test_exhausted_when_used_reaches_entitlement():
    allowance = compute_allowance(maximum_attempts=2, attempts_used=2)
    assert allowance.available_attempts == 0
    assert allowance.has_available_attempts is False


def test_used_above_the_entitlement_never_goes_negative():
    allowance = compute_allowance(maximum_attempts=2, attempts_used=5)
    assert allowance.available_attempts == 0


def test_unlimited_is_distinct_from_zero():
    allowance = compute_allowance(maximum_attempts=None, attempts_used=9)
    assert allowance.unlimited is True
    assert allowance.available_attempts is None
    assert allowance.has_available_attempts is True


@pytest.mark.parametrize("broken", [0, -1, "two", 1.5, True])
def test_a_broken_maximum_reads_as_unlimited_not_as_zero(broken):
    # A configuration defect must never silently tell a learner they are out of attempts.
    allowance = compute_allowance(maximum_attempts=broken, attempts_used=1)
    assert allowance.unlimited is True
    assert is_valid_maximum(broken) is False


def test_negative_inputs_are_clamped_rather_than_trusted():
    allowance = compute_allowance(maximum_attempts=2, attempts_used=-4, granted_attempts=-9)
    assert allowance.attempts_used == 0
    assert allowance.granted_attempts == 0
    assert allowance.available_attempts == 2


def test_only_active_grants_contribute():
    grants = (
        _grant("g1", 1),
        _grant("g2", 2).revoked(at="2026-01-02T00:00:00Z", by="admin-jo"),
    )
    assert total_granted_attempts(grants) == 1
    assert grants[1].status is GrantStatus.REVOKED


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


async def test_used_count_includes_in_flight_reservations(container, quiz, attempts, clock):
    """A RESERVED retake counts as an attempt used, even before UC-03 has created one.

    This is the window §15 describes: without it, two concurrent requests would both see the same
    free attempt.
    """
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)

    before = await container.services.allowances.attempts_used(
        learner_id=attempt.learner_id, course_id=attempt.course_id, quiz_id=attempt.quiz_id
    )
    assert before == 1

    await container.repositories.retakes.reserve(_reservation(attempt.attempt_id, clock))

    after = await container.services.allowances.attempts_used(
        learner_id=attempt.learner_id, course_id=attempt.course_id, quiz_id=attempt.quiz_id
    )
    assert after == 2


async def test_completed_reservations_are_not_double_counted(container, quiz, attempts, clock):
    """Once UC-03 has the attempt, UC-03's count is the only place it is counted."""
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)

    reservation = await container.repositories.retakes.reserve(
        _reservation(attempt.attempt_id, clock)
    )
    retake_attempt = attempts.start_attempt(question_ids=("q4", "q5", "q6"))
    await container.repositories.retakes.save(
        reservation.completed_with(attempt_id=retake_attempt.attempt_id, at=to_iso(clock.now()))
    )

    used = await container.services.allowances.attempts_used(
        learner_id=attempt.learner_id, course_id=attempt.course_id, quiz_id=attempt.quiz_id
    )
    # Two attempts exist upstream; the completed reservation adds nothing on top.
    assert used == 2


async def test_a_failed_reservation_releases_the_attempt(container, quiz, attempts, clock):
    """§14: a failed retake must not quietly cost the learner an attempt."""
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)

    reservation = await container.repositories.retakes.reserve(
        _reservation(attempt.attempt_id, clock)
    )
    await container.repositories.retakes.save(
        reservation.failed_with(code="ATTEMPT_CREATION_FAILED", message="boom", at=to_iso(clock.now()))
    )

    used = await container.services.allowances.attempts_used(
        learner_id=attempt.learner_id, course_id=attempt.course_id, quiz_id=attempt.quiz_id
    )
    assert used == 1


async def test_grants_are_scoped_to_learner_course_and_quiz(container):
    grants = container.repositories.grants
    await grants.insert(_grant("g1", 2, learner_id="learner-alice", quiz_id="quiz-1"))
    await grants.insert(_grant("g2", 5, learner_id="learner-bob", quiz_id="quiz-1"))
    await grants.insert(_grant("g3", 7, learner_id="learner-alice", quiz_id="quiz-2"))

    alice_quiz1 = await container.services.allowances.granted_attempts(
        "learner-alice", "course-1", "quiz-1"
    )
    assert alice_quiz1 == 2


async def test_broken_maximum_is_reported_as_an_anomaly(container, quiz, attempts):
    allowance, anomalies = await container.services.allowances.compute(
        learner_id="learner-alice",
        course_id="course-1",
        quiz_id="quiz-1",
        maximum_attempts=0,
    )
    assert allowance.unlimited is True
    assert [item.code.value for item in anomalies] == ["INVALID_ATTEMPT_ALLOWANCE"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grant(
    grant_id: str,
    additional: int,
    *,
    learner_id: str = "learner-alice",
    course_id: str = "course-1",
    quiz_id: str = "quiz-1",
) -> AdditionalAttemptGrant:
    return AdditionalAttemptGrant(
        grant_id=grant_id,
        learner_id=learner_id,
        course_id=course_id,
        quiz_id=quiz_id,
        additional_attempts=additional,
        granted_by="admin-jo",
        idempotency_key=f"grant:{learner_id}:{quiz_id}:{grant_id}",
        granted_at="2026-01-01T00:00:00Z",
    )


def _reservation(previous_attempt_id: str, clock, *, attempt_number: int = 2) -> RetakeRequest:
    now = to_iso(clock.now())
    return RetakeRequest(
        retake_id=f"retake-{previous_attempt_id}",
        idempotency_key=f"retake:learner-alice:quiz-1:{previous_attempt_id}",
        learner_id="learner-alice",
        course_id="course-1",
        quiz_id="quiz-1",
        previous_attempt_id=previous_attempt_id,
        attempt_number=attempt_number,
        configuration_version_id="cfg-v1",
        configuration_version_source=ConfigurationVersionSource.CARRIED_FORWARD,
        status=RetakeRequestStatus.RESERVED,
        requested_at=now,
        updated_at=now,
    )
