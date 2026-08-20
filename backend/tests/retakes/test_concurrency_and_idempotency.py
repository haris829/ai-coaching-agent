"""Concurrency, idempotency and failure recovery (§14, §15, §16).

The claims under test:

* two simultaneous retake requests produce **one** attempt, not two;
* a learner can never end up with more attempts than their allowance, however the requests
  interleave;
* a retried request returns the attempt that already exists;
* a failed retake does not cost the learner an attempt, and the same request can be sent again.

Concurrency is exercised with ``asyncio.gather`` over the real service and the real repository. The
in-memory repository holds its lock across the read-and-write of every mutating method, which is the
same guarantee the protocol requires of the company database — so these tests describe the contract,
not an artefact of the provisional store.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.errors import PersistenceFailedError, ProviderUnavailableError
from app.core.time import to_iso
from app.modules.retakes.domain.enums import RetakeRequestStatus
from app.modules.retakes.domain.errors import (
    AttemptCreationFailedError,
    AttemptSlotTakenError,
    DuplicateRetakeRequestError,
    NoAttemptsRemainingError,
    RetakeInProgressError,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# §16 — idempotency
# ---------------------------------------------------------------------------


async def test_a_repeated_request_returns_the_same_attempt(container, first_attempt, attempts):
    first = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )
    second = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert second.replayed is True
    assert second.retake.retake_id == first.retake.retake_id
    assert second.attempt.attempt_id == first.attempt.attempt_id
    # Exactly two attempts exist: the original and one retake.
    assert len(await attempts.list_attempts("learner-alice", "quiz-1")) == 2


async def test_the_key_is_derived_so_no_client_token_is_needed(container, first_attempt):
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )
    assert outcome.retake.idempotency_key == (
        f"retake:learner-alice:quiz-1:{first_attempt.attempt_id}"
    )


async def test_naming_the_previous_attempt_explicitly_resolves_to_the_same_retake(
    container, first_attempt
):
    implicit = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )
    explicit = await container.services.retakes.create(
        learner_id="learner-alice",
        quiz_id="quiz-1",
        previous_attempt_id=first_attempt.attempt_id,
    )
    assert explicit.replayed is True
    assert explicit.retake.retake_id == implicit.retake.retake_id


async def test_a_second_retake_after_submitting_is_a_different_request(
    container, configurations, attempts, bank
):
    """Retakes are keyed by the previous attempt, so the *next* one gets its own key."""
    configurations.publish(question_count=3, maximum_attempts=4)
    bank.add_many(12)
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)

    second = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )
    attempts.submit(second.attempt.attempt_id)
    third = await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert third.replayed is False
    assert third.retake.retake_id != second.retake.retake_id
    assert third.attempt.attempt_number == 3


# ---------------------------------------------------------------------------
# §15 — concurrency
# ---------------------------------------------------------------------------


async def test_two_simultaneous_requests_create_one_attempt(container, first_attempt, attempts):
    results = await asyncio.gather(
        container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1"),
        container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1"),
        return_exceptions=True,
    )

    created = [item for item in results if not isinstance(item, Exception)]
    # Either one succeeds and the other is told a retake is in progress, or the second finds the
    # completed record and replays it. Both are correct; two new attempts would not be.
    assert len(created) >= 1
    assert all(
        isinstance(item, RetakeInProgressError)
        for item in results
        if isinstance(item, Exception)
    )
    stored = await attempts.list_attempts("learner-alice", "quiz-1")
    assert len(stored) == 2
    assert sorted(attempt.attempt_number for attempt in stored) == [1, 2]


async def test_five_simultaneous_requests_still_create_one_attempt(
    container, first_attempt, attempts
):
    results = await asyncio.gather(
        *[
            container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")
            for _ in range(5)
        ],
        return_exceptions=True,
    )

    new_attempts = [
        attempt
        for attempt in await attempts.list_attempts("learner-alice", "quiz-1")
        if attempt.attempt_id != first_attempt.attempt_id
    ]
    assert len(new_attempts) == 1
    assert all(
        isinstance(item, RetakeInProgressError)
        for item in results
        if isinstance(item, Exception)
    )


async def test_the_allowance_holds_when_the_last_attempt_is_contested(
    container, configurations, attempts, bank
):
    """One attempt left and two requests for it. Exactly one may win."""
    configurations.publish(question_count=3, maximum_attempts=2)
    bank.add_many(12)
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)

    await asyncio.gather(
        container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1"),
        container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1"),
        return_exceptions=True,
    )

    stored = await attempts.list_attempts("learner-alice", "quiz-1")
    assert len(stored) == 2  # the maximum, not three


async def test_a_taken_attempt_slot_is_a_conflict_not_a_second_attempt(
    container, first_attempt, clock
):
    """The reservation constraint, exercised directly.

    A different retake request that has already claimed attempt number 2 makes the second
    reservation fail rather than letting both proceed.
    """
    from app.modules.retakes.domain.enums import ConfigurationVersionSource
    from app.modules.retakes.domain.requests import RetakeRequest

    now = to_iso(clock.now())
    squatter = RetakeRequest(
        retake_id="retake-squatter",
        idempotency_key="retake:learner-alice:quiz-1:some-other-attempt",
        learner_id="learner-alice",
        course_id="course-1",
        quiz_id="quiz-1",
        previous_attempt_id="some-other-attempt",
        attempt_number=2,
        configuration_version_id="cfg-v1",
        configuration_version_source=ConfigurationVersionSource.CARRIED_FORWARD,
        status=RetakeRequestStatus.RESERVED,
        requested_at=now,
        updated_at=now,
    )
    await container.repositories.retakes.reserve(squatter)

    # The squatting reservation is itself an in-flight retake, so the request is refused. Either
    # refusal is correct; creating a second attempt in slot 2 is not.
    with pytest.raises((RetakeInProgressError, AttemptSlotTakenError, NoAttemptsRemainingError)):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")


# ---------------------------------------------------------------------------
# §14 — failure and safe retry
# ---------------------------------------------------------------------------


async def test_a_creation_failure_releases_the_attempt_slot(container, first_attempt, attempts):
    attempts.creation_failure = RuntimeError("the attempt store fell over")

    with pytest.raises(AttemptCreationFailedError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert raised.value.retryable is True
    # The reservation is FAILED, so it no longer counts against the allowance.
    assert await container.repositories.retakes.count_active_reservations(
        "learner-alice", "quiz-1"
    ) == 0
    used = await container.services.allowances.attempts_used(
        learner_id="learner-alice", course_id="course-1", quiz_id="quiz-1"
    )
    assert used == 1


async def test_the_same_request_can_be_retried_after_a_failure(
    container, first_attempt, attempts
):
    attempts.creation_failure = RuntimeError("transient")
    with pytest.raises(AttemptCreationFailedError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    attempts.creation_failure = None
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert outcome.replayed is False
    assert outcome.retake.status is RetakeRequestStatus.COMPLETED
    assert outcome.retake.attempt_count == 2  # first go plus the retry
    assert outcome.attempt.attempt_number == 2
    # And still only two attempts: the retry did not create an extra one.
    assert len(await attempts.list_attempts("learner-alice", "quiz-1")) == 2


async def test_a_retry_reuses_the_same_retake_record(container, first_attempt, attempts):
    attempts.creation_failure = RuntimeError("transient")
    with pytest.raises(AttemptCreationFailedError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")
    failed = (
        await container.repositories.retakes.list_for_learner_quiz("learner-alice", "quiz-1")
    )[0]

    attempts.creation_failure = None
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert outcome.retake.retake_id == failed.retake_id
    records = await container.repositories.retakes.list_for_learner_quiz(
        "learner-alice", "quiz-1"
    )
    assert len(records) == 1


async def test_an_upstream_refusal_is_forwarded_unchanged_after_releasing(
    container, first_attempt, attempts
):
    """UC-03 expressing a refusal in the shared taxonomy is not rewrapped."""
    attempts.creation_failure = ProviderUnavailableError("UC-03 is restarting.")

    with pytest.raises(ProviderUnavailableError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert await container.repositories.retakes.count_active_reservations(
        "learner-alice", "quiz-1"
    ) == 0


async def test_a_failure_to_record_the_failure_leaves_the_slot_held(
    container, first_attempt, attempts, monkeypatch
):
    """The safe direction to fail in.

    If the reservation cannot be marked FAILED, it stays RESERVED: further retakes are blocked
    until an operator looks, rather than the learner being handed an extra attempt.
    """
    attempts.creation_failure = RuntimeError("transient")

    async def refuse_save(_request):
        raise PersistenceFailedError("retakes.test")

    monkeypatch.setattr(container.repositories.retakes, "save", refuse_save)

    with pytest.raises(AttemptCreationFailedError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert await container.repositories.retakes.count_active_reservations(
        "learner-alice", "quiz-1"
    ) == 1


async def test_a_reservation_never_records_an_attempt_it_did_not_create(
    container, first_attempt, attempts
):
    attempts.creation_failure = RuntimeError("boom")
    with pytest.raises(AttemptCreationFailedError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    record = (
        await container.repositories.retakes.list_for_learner_quiz("learner-alice", "quiz-1")
    )[0]
    assert record.status is RetakeRequestStatus.FAILED
    assert record.attempt_id is None
    assert record.failure_code == "ATTEMPT_CREATION_FAILED"


# ---------------------------------------------------------------------------
# The race window itself
# ---------------------------------------------------------------------------
#
# Between reading "has this already happened?" and taking the reservation there is a window in
# which a competing request can insert the same key. Cooperative scheduling will not reliably land
# two requests inside it, so these tests reproduce it exactly: a request reads its eligibility
# context, a competitor then completes the identical retake, and the first request proceeds on the
# context it read — which is what a real concurrent request would be holding.


def _freeze_race(container, monkeypatch, context) -> None:
    """Replay one request from the moment before its competitor won.

    Three things are pinned: the eligibility context it had already read, the key lookup answering
    as it did then, and the reservation raising the uniqueness violation the database would raise.
    """
    async def stale_context(learner_id: str, quiz_id: str):
        return context

    monkeypatch.setattr(container.services.eligibility, "load", stale_context)

    repository = container.repositories.retakes
    original_lookup = repository.get_by_idempotency_key
    calls = {"count": 0}

    async def lookup(key: str):
        calls["count"] += 1
        return None if calls["count"] == 1 else await original_lookup(key)

    monkeypatch.setattr(repository, "get_by_idempotency_key", lookup)

    async def already_taken(request):
        raise DuplicateRetakeRequestError(request.idempotency_key)

    monkeypatch.setattr(repository, "reserve", already_taken)


async def test_losing_the_key_race_to_a_completed_retake_replays_it(
    container, first_attempt, attempts, monkeypatch
):
    context = await container.services.eligibility.load("learner-alice", "quiz-1")
    winner = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )
    _freeze_race(container, monkeypatch, context)

    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    assert outcome.replayed is True
    assert outcome.retake.retake_id == winner.retake.retake_id
    assert outcome.attempt.attempt_id == winner.attempt.attempt_id
    assert outcome.attempt.delivered_question_ids == winner.attempt.delivered_question_ids
    # Still two attempts: the loser of the race created nothing.
    assert len(await attempts.list_attempts("learner-alice", "quiz-1")) == 2


async def test_losing_the_key_race_to_an_in_flight_retake_is_a_retryable_conflict(
    container, first_attempt, monkeypatch, clock
):
    """The competitor has the slot but no attempt yet, so there is nothing to replay."""
    from app.modules.retakes.domain.enums import ConfigurationVersionSource
    from app.modules.retakes.domain.requests import RetakeRequest

    context = await container.services.eligibility.load("learner-alice", "quiz-1")
    now = to_iso(clock.now())
    await container.repositories.retakes.reserve(
        RetakeRequest(
            retake_id="retake-competitor",
            idempotency_key=f"retake:learner-alice:quiz-1:{first_attempt.attempt_id}",
            learner_id="learner-alice",
            course_id="course-1",
            quiz_id="quiz-1",
            previous_attempt_id=first_attempt.attempt_id,
            attempt_number=2,
            configuration_version_id="cfg-v1",
            configuration_version_source=ConfigurationVersionSource.CARRIED_FORWARD,
            status=RetakeRequestStatus.RESERVED,
            requested_at=now,
            updated_at=now,
        )
    )
    _freeze_race(container, monkeypatch, context)

    with pytest.raises(RetakeInProgressError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert raised.value.context["retake_id"] == "retake-competitor"
    assert raised.value.retryable is True


async def test_losing_the_slot_race_is_a_retryable_conflict(
    container, first_attempt, attempts, monkeypatch
):
    """The attempt-number constraint is what actually holds the allowance under concurrency."""

    async def slot_gone(request):
        raise AttemptSlotTakenError(request.learner_id, request.quiz_id, request.attempt_number)

    monkeypatch.setattr(container.repositories.retakes, "reserve", slot_gone)

    with pytest.raises(AttemptSlotTakenError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert raised.value.retryable is True
    assert raised.value.context["attempt_number"] == 2
    # No attempt was created for the losing request.
    assert len(await attempts.list_attempts("learner-alice", "quiz-1")) == 1
