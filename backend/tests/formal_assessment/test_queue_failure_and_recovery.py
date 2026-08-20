"""The assessor queue outage (§13).

    PASS -> persist PENDING_REVIEW -> queue failure -> recoverable

The assertion that matters is not "the retry works" but "the assessment was never in the queue in the first
place": it is in the review repository, listed for the assessor, blocking the certificate, whether or not the
queue ever hears about it.
"""

from __future__ import annotations

import contextlib

import pytest

from app.modules.formal_assessment.domain.enums import (
    FormalAttemptState,
    QueuePublishState,
    ReviewState,
)
from app.modules.formal_assessment.domain.errors import ReviewQueueUnavailableError
from tests.formal_assessment.conftest import FormalFlow
from tests.formal_assessment.fakes import DEFAULT_ASSESSOR

pytestmark = pytest.mark.anyio


async def _pass_with_queue_down(flow: FormalFlow, container, passing, queue):
    await flow.to_active()
    passing(flow.attempt_id)
    queue.unavailable = True
    await flow.submit()
    review = await flow.review()
    assert review is not None
    return review


async def test_a_queue_outage_does_not_lose_the_pending_review(flow: FormalFlow, container, passing, queue):
    review = await _pass_with_queue_down(flow, container, passing, queue)

    assert review.state is ReviewState.PENDING_REVIEW
    assert review.publish_state is QueuePublishState.PENDING
    assert queue.pending_count() == 0, "nothing reached the queue"

    record = await flow.record()
    assert record.state is FormalAttemptState.PENDING_REVIEW
    assert record.review_id == review.review_id


async def test_the_learners_result_is_unaffected_by_the_queue(flow: FormalFlow, container, passing, queue):
    """The pass workflow does not wait for the queue and does not fail with it."""
    await flow.to_active()
    passing(flow.attempt_id)
    queue.unavailable = True
    outcome = await flow.submit()
    assert outcome.formal_attempt.state is FormalAttemptState.PENDING_REVIEW
    assert outcome.formal_attempt.result is not None
    assert outcome.formal_attempt.result.passed is True


async def test_an_unpublished_review_is_still_in_the_assessors_queue(flow: FormalFlow, container, passing, queue):
    review = await _pass_with_queue_down(flow, container, passing, queue)
    page = await container.services.reviews.list_pending(assessor_id=DEFAULT_ASSESSOR)
    assert [item.review_id for item in page.reviews] == [review.review_id]


async def test_an_unpublished_review_is_still_reviewable_and_decidable(flow: FormalFlow, container, passing, queue):
    """A queue outage delays a notification. It does not stop the work."""
    review = await _pass_with_queue_down(flow, container, passing, queue)
    detail = await container.services.reviews.get_detail(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id
    )
    assert detail.review.review_id == review.review_id

    outcome = await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="APPROVED"
    )
    assert outcome.review.state is ReviewState.APPROVED


async def test_the_certificate_stays_blocked_during_a_queue_outage(flow: FormalFlow, container, passing, queue):
    await _pass_with_queue_down(flow, container, passing, queue)
    eligibility = await container.services.certificates.check_eligibility(flow.formal_attempt_id)
    assert eligibility.certificate_allowed is False
    assert eligibility.reason is not None and eligibility.reason.value == "PENDING_HUMAN_REVIEW"


async def test_the_failure_is_audited_as_recoverable(flow: FormalFlow, container, passing, queue, audit):
    await _pass_with_queue_down(flow, container, passing, queue)
    assert "QUEUE_FAILURE" in audit.codes()
    fields = audit.fields_for("QUEUE_FAILURE")[-1]
    assert fields["recoverable"] is True
    assert fields["publish_attempts"] == 1


async def test_an_unpublished_review_appears_on_the_recovery_list(flow: FormalFlow, container, passing, queue):
    review = await _pass_with_queue_down(flow, container, passing, queue)
    recoverable = await container.services.recovery.list_recoverable()
    assert [item.review_id for item in recoverable] == [review.review_id]


async def test_retrying_while_the_queue_is_still_down_reports_the_failure(flow: FormalFlow, container, passing, queue, audit):
    review = await _pass_with_queue_down(flow, container, passing, queue)
    with pytest.raises(ReviewQueueUnavailableError) as error:
        await container.services.recovery.retry(review.review_id)
    assert error.value.status_code == 503
    assert error.value.retryable is True
    assert "QUEUE_RETRY" in audit.codes()

    still_there = await container.services.recovery.list_recoverable()
    assert len(still_there) == 1


async def test_retrying_after_the_queue_recovers_publishes_it(flow: FormalFlow, container, passing, queue):
    review = await _pass_with_queue_down(flow, container, passing, queue)
    queue.unavailable = False
    republished = await container.services.recovery.retry(review.review_id)
    assert republished.publish_state is QueuePublishState.PUBLISHED
    assert republished.published_at is not None
    assert queue.keys() == [f"formal-review:{review.review_id}"]
    assert await container.services.recovery.list_recoverable() == ()


async def test_the_sweep_works_through_a_backlog(flow: FormalFlow, container, passing, queue, policies):
    policies.publish("quiz-formal-2", course_id="course-1")
    first = await _pass_with_queue_down(flow, container, passing, queue)

    second_flow = FormalFlow(container=container, quiz_id="quiz-formal-2")
    await second_flow.to_active()
    passing(second_flow.attempt_id)
    await second_flow.submit()
    second = await second_flow.review()
    assert second is not None

    assert len(await container.services.recovery.list_recoverable()) == 2

    queue.unavailable = False
    report = await container.services.recovery.sweep()
    assert report.considered == 2
    assert report.published == 2
    assert report.still_pending == 0
    assert set(report.review_ids) == {first.review_id, second.review_id}
    assert await container.services.recovery.list_recoverable() == ()


async def test_the_sweep_does_not_stop_at_the_first_failure(flow: FormalFlow, container, passing, queue, policies):
    policies.publish("quiz-formal-2", course_id="course-1")
    await _pass_with_queue_down(flow, container, passing, queue)
    second_flow = FormalFlow(container=container, quiz_id="quiz-formal-2")
    await second_flow.to_active()
    passing(second_flow.attempt_id)
    await second_flow.submit()

    report = await container.services.recovery.sweep()
    assert report.considered == 2
    assert report.published == 0
    assert report.still_pending == 2, "a sweep over a down queue reports, it does not raise"


async def test_repeated_failures_park_the_review_without_discarding_it(flow: FormalFlow, container, passing, queue):
    """The attempt ceiling bounds retry noise, not durability."""
    review = await _pass_with_queue_down(flow, container, passing, queue)
    for _ in range(5):
        with contextlib.suppress(ReviewQueueUnavailableError):
            await container.services.recovery.retry(review.review_id)

    parked = await container.repositories.reviews.get(review.review_id)
    assert parked is not None
    assert parked.publish_state is QueuePublishState.FAILED
    assert parked.publish_attempts >= 3
    assert parked.last_publish_error
    # Still recoverable, still reviewable, still blocking the certificate.
    assert [item.review_id for item in await container.services.recovery.list_recoverable()] == [
        review.review_id
    ]
    page = await container.services.reviews.list_pending(assessor_id=DEFAULT_ASSESSOR)
    assert len(page.reviews) == 1


async def test_publishing_twice_creates_one_queue_entry(flow: FormalFlow, container, passing, queue):
    """§20: the same pending assessment must not be inserted into the queue twice."""
    await flow.to_active()
    passing(flow.attempt_id)
    await flow.submit()
    review = await flow.review()
    assert review is not None
    assert review.publish_state is QueuePublishState.PUBLISHED

    # A retry on an already-published review is a no-op rather than a second entry.
    again = await container.services.recovery.retry(review.review_id)
    assert again.publish_state is QueuePublishState.PUBLISHED
    assert len(queue.keys()) == 1


async def test_the_queue_entry_carries_no_personal_data(flow: FormalFlow, container, passing, queue):
    """A queue is often less guarded than a database; everything sensitive is one authorised read away."""
    await flow.to_active()
    passing(flow.attempt_id)
    await flow.submit()
    entry = queue.entries[0]
    rendered = str(entry.as_dict())
    assert "John Smith" not in rendered
    assert "john.smith@example.com" not in rendered
    assert entry.review_id
    assert entry.entry_key == f"formal-review:{entry.review_id}"


async def test_a_recovery_retry_cannot_change_a_decision(flow: FormalFlow, container, passing, queue):
    review = await _pass_with_queue_down(flow, container, passing, queue)
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="REQUIRES_FURTHER_REVIEW"
    )
    queue.unavailable = False
    republished = await container.services.recovery.retry(review.review_id)
    assert republished.state is ReviewState.REQUIRES_FURTHER_REVIEW
    record = await flow.record()
    assert record.certificate_allowed is False
