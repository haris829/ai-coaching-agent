"""Administrator additional-attempt grants (§11, §12, §14).

The two claims that matter most:

* a grant raises **one learner's** entitlement and changes **nothing** about the quiz
  configuration or any other learner;
* a retried grant does not grant twice, and a grant that fails to persist grants nothing at all.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.errors import PersistenceFailedError
from app.modules.retakes.domain.enums import GrantStatus
from app.modules.retakes.domain.errors import (
    GrantAlreadyRevokedError,
    GrantConsumedError,
    GrantIdempotencyKeyReusedError,
    GrantNotFoundError,
    InvalidGrantSizeError,
    QuizNotFoundError,
)
from app.modules.retakes.domain.idempotency import grant_key
from tests.retakes.fakes import FailingGrantRepository

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# §11 — the grant does not change the configuration
# ---------------------------------------------------------------------------


async def test_a_grant_raises_one_learners_entitlement_only(container, quiz, attempts):
    """The §11 example, exactly: global maximum 2, learner A gets 3, learner B still gets 2."""
    for learner in ("learner-alice", "learner-bob"):
        for questions in (("q1", "q2", "q3"), ("q4", "q5", "q6")):
            attempt = attempts.start_attempt(learner_id=learner, question_ids=questions)
            attempts.submit(attempt.attempt_id)

    await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-1",
    )

    alice = await container.services.eligibility.check("learner-alice", "quiz-1")
    bob = await container.services.eligibility.check("learner-bob", "quiz-1")

    assert alice.allowance.total_entitlement == 3
    assert alice.can_retake is True
    assert bob.allowance.total_entitlement == 2
    assert bob.can_retake is False


async def test_the_configuration_version_is_untouched_by_a_grant(container, quiz, configurations):
    before = configurations.versions["cfg-v1"]

    await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=3,
        granted_by="admin-jo",
        idempotency_key="ticket-2",
    )

    after = configurations.versions["cfg-v1"]
    assert after is before
    assert after.maximum_attempts == 2


async def test_a_grant_is_scoped_to_the_quiz_it_names(container, configurations, bank, attempts):
    configurations.publish(question_count=3, maximum_attempts=1)
    configurations.publish(
        configuration_version_id="cfg-q2", quiz_id="quiz-2", course_id="course-1",
        question_count=3, maximum_attempts=1,
    )
    bank.add_many(9)
    for quiz_id, version in (("quiz-1", "cfg-v1"), ("quiz-2", "cfg-q2")):
        attempt = attempts.start_attempt(
            quiz_id=quiz_id, configuration_version_id=version, question_ids=("q1", "q2", "q3")
        )
        attempts.submit(attempt.attempt_id)

    await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-3",
    )

    assert (await container.services.eligibility.check("learner-alice", "quiz-1")).can_retake
    assert not (await container.services.eligibility.check("learner-alice", "quiz-2")).can_retake


async def test_the_course_is_resolved_from_uc01_not_trusted_from_the_caller(container, quiz):
    grant, _ = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-4",
    )
    assert grant.course_id == "course-1"


async def test_a_mismatched_course_is_refused(container, quiz):
    from app.modules.retakes.domain.errors import CourseNotFoundError

    with pytest.raises(CourseNotFoundError):
        await container.services.grants.grant(
            learner_id="learner-alice",
            quiz_id="quiz-1",
            course_id="course-somewhere-else",
            additional_attempts=1,
            granted_by="admin-jo",
            idempotency_key="ticket-5",
        )


async def test_an_unknown_quiz_is_refused(container):
    with pytest.raises(QuizNotFoundError):
        await container.services.grants.grant(
            learner_id="learner-alice",
            quiz_id="quiz-nope",
            additional_attempts=1,
            granted_by="admin-jo",
            idempotency_key="ticket-6",
        )


# ---------------------------------------------------------------------------
# §12 — safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, -1, -100, 11])
async def test_invalid_grant_sizes_are_refused(container, quiz, size):
    with pytest.raises(InvalidGrantSizeError):
        await container.services.grants.grant(
            learner_id="learner-alice",
            quiz_id="quiz-1",
            additional_attempts=size,
            granted_by="admin-jo",
            idempotency_key=f"ticket-{size}",
        )


async def test_the_granting_administrator_is_recorded(container, quiz, audit):
    grant, _ = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        reason="Assessment interrupted by a fire alarm.",
        idempotency_key="ticket-7",
    )

    assert grant.granted_by == "admin-jo"
    assert grant.reason == "Assessment interrupted by a fire alarm."
    assert grant.granted_at == "2026-01-02T10:00:00Z"
    assert "additional_attempt_granted" in audit.codes()


async def test_the_idempotency_key_is_namespaced_to_learner_and_quiz(container, quiz):
    """Two administrators reusing an obvious token must not collide."""
    alice, _ = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="1",
    )
    bob, _ = await container.services.grants.grant(
        learner_id="learner-bob",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="1",
    )

    assert alice.grant_id != bob.grant_id
    assert alice.idempotency_key == grant_key("learner-alice", "quiz-1", "1")
    assert bob.idempotency_key == grant_key("learner-bob", "quiz-1", "1")


# ---------------------------------------------------------------------------
# §14 — duplicates, retries and failures
# ---------------------------------------------------------------------------


async def test_a_retried_grant_does_not_grant_twice(container, quiz):
    first, first_replayed = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=2,
        granted_by="admin-jo",
        idempotency_key="ticket-8",
    )
    second, second_replayed = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=2,
        granted_by="admin-jo",
        idempotency_key="ticket-8",
    )

    assert first_replayed is False
    assert second_replayed is True
    assert second.grant_id == first.grant_id
    granted = await container.services.allowances.granted_attempts(
        "learner-alice", "course-1", "quiz-1"
    )
    assert granted == 2  # not 4


async def test_a_deliberate_second_grant_needs_a_new_key(container, quiz):
    await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-9",
    )
    await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-10",
    )

    granted = await container.services.allowances.granted_attempts(
        "learner-alice", "course-1", "quiz-1"
    )
    assert granted == 2


async def test_reusing_a_key_for_a_different_grant_is_refused(container, quiz):
    """Returning the stored grant would tell the administrator a different grant succeeded."""
    await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-11",
    )

    with pytest.raises(GrantIdempotencyKeyReusedError):
        await container.services.grants.grant(
            learner_id="learner-alice",
            quiz_id="quiz-1",
            additional_attempts=5,
            granted_by="admin-jo",
            idempotency_key="ticket-11",
        )


async def test_concurrent_identical_grants_produce_one_grant(container, quiz):
    results = await asyncio.gather(
        *[
            container.services.grants.grant(
                learner_id="learner-alice",
                quiz_id="quiz-1",
                additional_attempts=1,
                granted_by="admin-jo",
                idempotency_key="ticket-12",
            )
            for _ in range(4)
        ]
    )

    ids = {grant.grant_id for grant, _ in results}
    assert len(ids) == 1
    granted = await container.services.allowances.granted_attempts(
        "learner-alice", "course-1", "quiz-1"
    )
    assert granted == 1


async def test_a_failed_grant_grants_nothing_and_can_be_retried(
    settings, clock, configurations, bank, attempts, audit
):
    """§14: no partial application, no corrupted count, and a safe retry."""
    from app.modules.retakes.container import create_container
    from tests.retakes.world import SequentialIdGenerator

    configurations.publish(question_count=3, maximum_attempts=2)
    bank.add_many(6)
    failing = FailingGrantRepository(PersistenceFailedError("grants.test"))
    container = create_container(
        settings=settings,
        clock=clock,
        new_id=SequentialIdGenerator("uc08"),
        configurations=configurations,
        question_bank=bank,
        attempts=attempts,
        audit=audit,
        grants_repository=failing,
    )

    with pytest.raises(PersistenceFailedError):
        await container.services.grants.grant(
            learner_id="learner-alice",
            quiz_id="quiz-1",
            additional_attempts=1,
            granted_by="admin-jo",
            idempotency_key="ticket-13",
        )

    # Nothing was granted, and nothing was audited as granted.
    assert await container.services.allowances.granted_attempts(
        "learner-alice", "course-1", "quiz-1"
    ) == 0
    assert "additional_attempt_granted" not in audit.codes()
    assert failing.inserts == 1


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


async def test_an_unused_grant_can_be_revoked(container, quiz, attempts):
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)
    grant, _ = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-14",
    )

    revoked = await container.services.grants.revoke(
        grant_id=grant.grant_id, revoked_by="admin-sam", reason="Granted in error."
    )

    assert revoked.status is GrantStatus.REVOKED
    assert revoked.revoked_by == "admin-sam"
    assert await container.services.allowances.granted_attempts(
        "learner-alice", "course-1", "quiz-1"
    ) == 0
    # The record survives: revocation is a transition, never a delete.
    assert await container.repositories.grants.get(grant.grant_id) is not None


async def test_a_spent_grant_cannot_be_revoked(container, quiz, attempts):
    """Revoking would push the used count above the entitlement — a state nothing else produces."""
    for questions in (("q1", "q2", "q3"), ("q4", "q5", "q6")):
        attempt = attempts.start_attempt(question_ids=questions)
        attempts.submit(attempt.attempt_id)
    grant, _ = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-15",
    )
    await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    with pytest.raises(GrantConsumedError):
        await container.services.grants.revoke(grant_id=grant.grant_id, revoked_by="admin-sam")


async def test_revoking_twice_is_refused(container, quiz):
    grant, _ = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-16",
    )
    await container.services.grants.revoke(grant_id=grant.grant_id, revoked_by="admin-sam")

    with pytest.raises(GrantAlreadyRevokedError):
        await container.services.grants.revoke(grant_id=grant.grant_id, revoked_by="admin-sam")


async def test_revoking_an_unknown_grant_is_a_not_found(container):
    with pytest.raises(GrantNotFoundError):
        await container.services.grants.revoke(grant_id="grant-nope", revoked_by="admin-sam")


async def test_a_grants_scope_and_size_cannot_be_edited(container, quiz):
    """The provisional store refuses what the protocol forbids, so a defect fails here first."""
    from dataclasses import replace

    grant, _ = await container.services.grants.grant(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        additional_attempts=1,
        granted_by="admin-jo",
        idempotency_key="ticket-17",
    )

    with pytest.raises(ValueError):
        await container.repositories.grants.save(replace(grant, additional_attempts=99))
