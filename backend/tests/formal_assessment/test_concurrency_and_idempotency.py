"""Idempotency and concurrency (§20).

The six races the specification names, each with a test that asserts the *count* rather than the absence of an
error: one submission, one auto-submission, one authoritative session, one decision, one certificate, one queue
entry. A loser's refusal is acceptable; a duplicate is not.

``FailingOnceRepository`` drives the compare-and-set recovery paths deterministically, which is how the "read the
winner rather than retrying blindly" behaviour gets exercised without relying on scheduling luck.
"""

from __future__ import annotations

import asyncio

import pytest

from app.modules.formal_assessment.container import create_container
from app.modules.formal_assessment.domain.enums import FormalAttemptState
from app.modules.formal_assessment.domain.errors import (
    ConcurrentModificationError,
    DuplicateFormalAttemptError,
    DuplicateReviewError,
)
from app.modules.formal_assessment.domain.idempotency import (
    certificate_key,
    formal_attempt_key,
    is_usable_client_request_id,
    review_key,
    session_registration_key,
    submission_key,
)
from app.modules.formal_assessment.ids import SequentialIdGenerator, SequentialTokenGenerator
from tests.formal_assessment.conftest import ALL_CONDITION_CODES, FormalFlow
from tests.formal_assessment.fakes import (
    DEFAULT_ASSESSOR,
    DEFAULT_LEARNER,
    DEFAULT_QUIZ,
    FailingOnceRepository,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Derived keys
# ---------------------------------------------------------------------------


def test_the_derived_keys_are_stable_and_scoped():
    assert formal_attempt_key("l1", "q1") == "formal-attempt:l1:q1"
    assert review_key("fa1") == "formal-review:fa1"
    assert certificate_key("fa1") == "formal-certificate:fa1"
    assert submission_key("a1") == "formal-submission:a1"
    assert session_registration_key("fa1", "a-long-enough-token") == (
        "session-registration:fa1:a-long-enough-token"
    )


def test_a_registration_token_must_be_unguessable():
    assert is_usable_client_request_id("x" * 16) is True
    assert is_usable_client_request_id("short") is False
    assert is_usable_client_request_id("x" * 200) is False
    assert is_usable_client_request_id(None) is False
    with pytest.raises(ValueError):
        session_registration_key("fa1", "short")


def test_a_registration_token_is_namespaced_to_its_formal_attempt():
    """Without the namespace, a client reusing "1" across assessments could replay the wrong session."""
    assert session_registration_key("fa1", "token-aaaaaaaaaaaa") != session_registration_key(
        "fa2", "token-aaaaaaaaaaaa"
    )


def test_a_blank_key_component_is_refused():
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            formal_attempt_key(bad, "q1")
        with pytest.raises(ValueError):
            review_key(bad)


# ---------------------------------------------------------------------------
# Repository constraints
# ---------------------------------------------------------------------------


async def test_the_repository_refuses_a_second_open_formal_attempt(container, flow: FormalFlow):
    from app.modules.formal_assessment.domain.attempt import new_formal_attempt

    await flow.acknowledge()
    duplicate = new_formal_attempt(
        formal_attempt_id="fa-duplicate",
        learner_id=DEFAULT_LEARNER,
        course_id="course-1",
        quiz_id=DEFAULT_QUIZ,
        idempotency_key=formal_attempt_key(DEFAULT_LEARNER, DEFAULT_QUIZ),
        now="2026-03-01T09:00:00.000Z",
    )
    with pytest.raises(DuplicateFormalAttemptError):
        await container.repositories.formal_attempts.insert(duplicate)


async def test_a_finished_sitting_frees_the_learner_to_sit_again(container, flow: FormalFlow, passing):
    """The open-state constraint is partial, so a formal retake is possible without deleting anything."""
    await flow.to_active()
    passing(flow.attempt_id)
    await flow.submit()

    second = FormalFlow(container=container)
    record = await second.acknowledge()
    assert record.formal_attempt_id != flow.formal_attempt_id
    assert len(await container.repositories.formal_attempts.list_for_learner(DEFAULT_LEARNER)) == 2


async def test_the_repository_refuses_a_second_review_for_one_attempt(container, flow: FormalFlow, passing):
    from app.modules.formal_assessment.domain.review import new_formal_review

    await flow.to_active()
    passing(flow.attempt_id)
    await flow.submit()

    duplicate = new_formal_review(
        review_id="review-duplicate",
        formal_attempt_id=flow.formal_attempt_id,
        learner_id=DEFAULT_LEARNER,
        course_id="course-1",
        quiz_id=DEFAULT_QUIZ,
        attempt_id=flow.attempt_id,
        now="2026-03-01T09:40:00.000Z",
    )
    with pytest.raises(DuplicateReviewError):
        await container.repositories.reviews.insert(duplicate)


async def test_a_stale_write_is_refused_by_the_compare_and_set(container, flow: FormalFlow):
    """§20: the mechanism every race in this file resolves through."""
    stale = await flow.acknowledge()
    await container.repositories.formal_attempts.save(
        stale.with_session("session-x", now="2026-03-01T09:01:00.000Z")
    )
    with pytest.raises(ConcurrentModificationError) as error:
        await container.repositories.formal_attempts.save(
            stale.with_session("session-y", now="2026-03-01T09:02:00.000Z")
        )
    assert error.value.code == "CONCURRENT_MODIFICATION"
    assert error.value.retryable is True


async def test_a_session_cannot_be_reactivated(container, flow: FormalFlow):
    """Reactivating a session is how a disconnected attempt would be resumed."""
    from dataclasses import replace

    from app.modules.formal_assessment.domain.enums import DeviceSessionState
    from app.modules.formal_assessment.domain.errors import DeviceSessionAlreadyHeldError

    await flow.to_active()
    await flow.submit()
    sessions = await container.repositories.sessions.list_for_attempt(flow.formal_attempt_id)
    closed = sessions[0]
    with pytest.raises(DeviceSessionAlreadyHeldError):
        await container.repositories.sessions.save(
            replace(closed, state=DeviceSessionState.ACTIVE, version=closed.version + 1)
        )


# ---------------------------------------------------------------------------
# Racing operations
# ---------------------------------------------------------------------------


async def test_two_acknowledgements_converge_on_one_record(container):
    results = await asyncio.gather(
        container.services.conditions.acknowledge(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, acknowledged_codes=ALL_CONDITION_CODES
        ),
        container.services.conditions.acknowledge(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, acknowledged_codes=ALL_CONDITION_CODES
        ),
        return_exceptions=True,
    )
    successes = [item for item in results if not isinstance(item, Exception)]
    assert successes, "at least one acknowledgement must succeed"
    stored = await container.repositories.formal_attempts.list_for_learner(DEFAULT_LEARNER)
    assert len(stored) == 1


async def test_acknowledging_recovers_from_a_lost_race(container, flow: FormalFlow):
    """The insert loses to a concurrent one; the service reads the winner instead of failing the learner."""
    await flow.acknowledge()
    original = container.repositories.formal_attempts
    container.services.conditions._attempts = FailingOnceRepository(
        original, "save", ConcurrentModificationError(record="formal_attempt", identifier="uc09-0001")
    )
    record = await flow.acknowledge()
    assert record.state is FormalAttemptState.CONDITIONS_ACKNOWLEDGED
    assert len(await original.list_for_learner(DEFAULT_LEARNER)) == 1


async def test_a_submission_that_loses_the_cas_reports_the_winners_outcome(container, flow: FormalFlow, upstream):
    await flow.to_active()
    original = container.repositories.formal_attempts

    class LosingOnce:
        """Applies the write, then reports a conflict — as a database would if another writer beat us."""

        def __init__(self) -> None:
            self._done = False

        def __getattr__(self, name: str):
            return getattr(original, name)

        async def save(self, record):
            if not self._done:
                self._done = True
                await original.save(record)
                raise ConcurrentModificationError(
                    record="formal_attempt", identifier=record.formal_attempt_id
                )
            return await original.save(record)

    container.services.attempts._attempts = LosingOnce()
    outcome = await flow.submit()
    assert outcome.formal_attempt.submitted is True
    assert outcome.replayed is True
    assert len(upstream.submissions) == 1


async def test_the_six_named_races_each_resolve_to_one(container, flow: FormalFlow, passing, upstream, certificates, queue, assessors):
    """A single scenario that exercises §20's list end to end and counts the results."""
    # 1. Device registration race — two starts, one active session.
    await flow.acknowledge()
    await flow.confirm_identity()
    from app.modules.formal_assessment.domain.device import DeviceDescriptor

    starts = await asyncio.gather(
        container.services.attempts.start(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, device=DeviceDescriptor(fingerprint="a")
        ),
        container.services.attempts.start(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, device=DeviceDescriptor(fingerprint="b")
        ),
        return_exceptions=True,
    )
    started = [item for item in starts if not isinstance(item, Exception)]
    assert len(started) == 1
    flow.formal_attempt_id = started[0].formal_attempt.formal_attempt_id
    flow.attempt_id = started[0].formal_attempt.attempt_id or ""
    flow.session_token = started[0].session.session_token
    assert len(upstream.attempts) == 1

    passing(flow.attempt_id)

    # 2. Duplicate submission — one submission.
    await asyncio.gather(flow.submit(), flow.submit(), return_exceptions=True)
    assert len(upstream.submissions) == 1

    # 3. Duplicate disconnect — still one submission.
    await asyncio.gather(
        container.services.attempts.handle_disconnect_by_id(
            formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:a"
        ),
        container.services.attempts.handle_disconnect_by_id(
            formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:b"
        ),
        return_exceptions=True,
    )
    assert len(upstream.submissions) == 1

    # 4. Queue race — one entry.
    await asyncio.gather(
        container.services.results.resolve_by_id(flow.formal_attempt_id),
        container.services.results.resolve_by_id(flow.formal_attempt_id),
        return_exceptions=True,
    )
    assert len(queue.keys()) == 1
    reviews = await container.repositories.reviews.list_pending()
    assert len(reviews) == 1

    # 5. Assessor decision race — one decision.
    assessors.add("assessor-second")
    review_id = reviews[0].review_id
    decisions = await asyncio.gather(
        container.services.reviews.decide(
            assessor_id=DEFAULT_ASSESSOR, review_id=review_id, decision="APPROVED"
        ),
        container.services.reviews.decide(
            assessor_id="assessor-second", review_id=review_id, decision="APPROVED"
        ),
        return_exceptions=True,
    )
    assert len([item for item in decisions if not isinstance(item, Exception)]) == 1

    # 6. Certificate race — one certificate.
    await asyncio.gather(
        container.services.certificates.trigger_by_id(flow.formal_attempt_id),
        container.services.certificates.trigger_by_id(flow.formal_attempt_id),
        return_exceptions=True,
    )
    assert certificates.certificate_count == 1


async def test_the_unconfigured_container_refuses_everything_consequential():
    """An unwired deployment starts and does nothing dangerous — see the container docstring."""
    container = create_container(
        new_id=SequentialIdGenerator("uc09"), new_token=SequentialTokenGenerator("token")
    )
    with pytest.raises(Exception) as quiz_error:
        await container.services.conditions.acknowledge(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, acknowledged_codes=ALL_CONDITION_CODES
        )
    assert quiz_error.value.code == "QUIZ_NOT_FOUND", "no policy provider means no formal assessments"

    # Nobody is an authorised assessor.
    with pytest.raises(Exception) as assessor_error:
        await container.services.reviews.list_pending(assessor_id="anyone-at-all")
    assert assessor_error.value.code == "ASSESSOR_NOT_AUTHORIZED"

    # And an attempt nobody has recorded gets no certificate opinion, defaulting to not-allowed.
    eligibility = await container.services.certificates.check_eligibility_for_attempt("attempt-1")
    assert eligibility.certificate_allowed is False
