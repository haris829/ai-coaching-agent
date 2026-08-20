"""The formal result and the pending-review state (§8, §9).

The rule under test is one line of the specification — ``PASS -> PENDING_REVIEW`` — and everything here is
about making sure nothing skips it: not a pass, not a resolution that runs twice, not a queue outage.
"""

from __future__ import annotations

import asyncio

import pytest

from app.modules.formal_assessment.domain.enums import (
    FormalAttemptState,
    QueuePublishState,
    ReviewState,
)
from app.modules.formal_assessment.services.result_service import ResolutionOutcome
from tests.formal_assessment.conftest import FormalFlow
from tests.formal_assessment.fakes import DEFAULT_LEARNER

pytestmark = pytest.mark.anyio


async def test_a_passing_formal_assessment_becomes_pending_review(flow: FormalFlow, container, passing, audit):
    """§8, §9: a pass does not produce a certificate. It produces a review."""
    await flow.to_active()
    passing(flow.attempt_id)
    outcome = await flow.submit()

    record = outcome.formal_attempt
    assert record.state is FormalAttemptState.PENDING_REVIEW
    assert record.result is not None
    assert record.result.passed is True
    assert record.result.percentage == 90.0
    assert record.certificate_allowed is False
    assert record.certificate_workflow_triggered_at is None
    assert record.review_id is not None

    codes = audit.codes()
    assert "RESULT_CALCULATED" in codes
    assert "PENDING_REVIEW_CREATED" in codes
    assert "CERTIFICATE_WORKFLOW_TRIGGERED" not in codes


async def test_the_review_record_carries_what_the_queue_and_the_list_need(flow: FormalFlow, passing):
    await flow.to_active()
    passing(flow.attempt_id)
    await flow.submit()

    review = await flow.review()
    assert review is not None
    assert review.state is ReviewState.PENDING_REVIEW
    assert review.percentage == 90.0
    assert review.formal_attempt_id == flow.formal_attempt_id
    assert review.attempt_id == flow.attempt_id
    assert review.publish_state is QueuePublishState.PUBLISHED


async def test_a_failing_formal_assessment_creates_no_review(flow: FormalFlow, passing, queue):
    await flow.to_active()
    passing(flow.attempt_id, passed=False, percentage=40.0)
    outcome = await flow.submit()

    assert outcome.formal_attempt.state is FormalAttemptState.FAILED
    assert outcome.formal_attempt.review_id is None
    assert await flow.review() is None
    assert queue.pending_count() == 0


async def test_the_result_is_copied_from_uc04_and_uc05_not_recalculated(flow: FormalFlow, scores, results):
    """§8: UC-09 has no scoring engine. It records what the existing ones decided."""
    await flow.to_active()
    scores.record(flow.attempt_id, percentage=83.5, total_marks=8.35, maximum_marks=10.0)
    results.record(flow.attempt_id, status="PASSED", percentage=83.5, pass_mark=80.0)
    outcome = await flow.submit()

    result = outcome.formal_attempt.result
    assert result is not None
    assert result.percentage == 83.5
    assert result.pass_mark == 80.0
    assert result.total_marks == 8.35
    assert result.maximum_marks == 10.0
    assert result.score_status == "CONFIRMED"
    assert result.result_id == f"result-{flow.attempt_id}"


async def test_an_unconfirmed_score_defers_rather_than_guessing(flow: FormalFlow, container, scores, results):
    await flow.to_active()
    scores.record(flow.attempt_id, status="PENDING")
    results.record(flow.attempt_id, status="PASSED")
    outcome = await flow.submit()

    assert outcome.formal_attempt.state is FormalAttemptState.SUBMITTED
    assert outcome.formal_attempt.result is None

    resolution = await container.services.results.resolve_by_id(flow.formal_attempt_id)
    assert resolution.outcome is ResolutionOutcome.DEFERRED
    assert resolution.reason == "SCORE_NOT_CONFIRMED"


async def test_an_undetermined_pass_fail_result_defers(flow: FormalFlow, container, scores, results):
    """UC-05 uses PENDING for "no safe decision is possible yet"; UC-09 waits rather than inventing one."""
    await flow.to_active()
    scores.record(flow.attempt_id)
    results.record(flow.attempt_id, status="PENDING")
    await flow.submit()

    resolution = await container.services.results.resolve_by_id(flow.formal_attempt_id)
    assert resolution.outcome is ResolutionOutcome.DEFERRED
    assert resolution.reason == "RESULT_NOT_DETERMINED"
    assert resolution.formal_attempt.state is FormalAttemptState.SUBMITTED


async def test_a_deferred_result_resolves_later_when_scoring_confirms(flow: FormalFlow, container, scores, results):
    await flow.to_active()
    await flow.submit()
    record = await flow.record()
    assert record.state is FormalAttemptState.SUBMITTED

    scores.record(flow.attempt_id)
    results.record(flow.attempt_id)
    resolution = await container.services.results.resolve_by_id(flow.formal_attempt_id)
    assert resolution.outcome is ResolutionOutcome.RESOLVED
    assert resolution.formal_attempt.state is FormalAttemptState.PENDING_REVIEW


async def test_reading_the_status_resolves_a_pending_result(flow: FormalFlow, container, scores, results):
    """Scoring is asynchronous, so the honest moment to look is whenever somebody asks."""
    await flow.to_active()
    await flow.submit()
    scores.record(flow.attempt_id)
    results.record(flow.attempt_id)

    status = await container.services.attempts.status(DEFAULT_LEARNER, flow.formal_attempt_id)
    assert status.formal_attempt.state is FormalAttemptState.PENDING_REVIEW


async def test_resolving_twice_produces_one_review(flow: FormalFlow, container, passing, queue):
    await flow.to_active()
    passing(flow.attempt_id)
    await flow.submit()

    again = await container.services.results.resolve_by_id(flow.formal_attempt_id)
    assert again.outcome is ResolutionOutcome.ALREADY_RESOLVED
    assert len(queue.keys()) == 1
    assert len(set(queue.keys())) == 1


async def test_concurrent_resolutions_produce_one_review_and_one_queue_entry(
    flow: FormalFlow, container, passing, queue
):
    """§20: the same pending assessment must not be inserted twice."""
    await flow.to_active()
    passing(flow.attempt_id)
    record = await flow.record()
    # Submit without the automatic resolution, then race two resolutions at it.
    submitted = await container.services.attempts.submit(
        learner_id=DEFAULT_LEARNER,
        formal_attempt_id=record.formal_attempt_id,
        session_token=flow.session_token,
    )
    assert submitted.formal_attempt.state is FormalAttemptState.PENDING_REVIEW

    await asyncio.gather(
        container.services.results.resolve_by_id(flow.formal_attempt_id),
        container.services.results.resolve_by_id(flow.formal_attempt_id),
        return_exceptions=True,
    )
    reviews = await container.repositories.reviews.list_pending()
    assert len(reviews) == 1
    assert len(queue.keys()) == 1


async def test_an_auto_submitted_pass_is_still_only_pending_review(flow: FormalFlow, container, passing):
    await flow.to_active()
    passing(flow.attempt_id)
    outcome = await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    assert outcome.formal_attempt.state is FormalAttemptState.PENDING_REVIEW
    review = await flow.review()
    assert review is not None
    assert review.auto_submitted is True
    assert review.anomaly_count >= 1


async def test_an_unreachable_scoring_module_does_not_fail_the_submission(flow: FormalFlow, scores, upstream):
    """A learner's submission has already succeeded; a scoring outage must not turn that into an error."""
    await flow.to_active()
    scores.fail_with = RuntimeError("UC-04 unavailable")
    outcome = await flow.submit()
    assert outcome.formal_attempt.state is FormalAttemptState.SUBMITTED
    assert upstream.snapshot(flow.attempt_id)["submitted"] is True


async def test_resolution_before_submission_is_not_applicable(flow: FormalFlow, container, passing):
    await flow.to_active()
    passing(flow.attempt_id)
    resolution = await container.services.results.resolve_by_id(flow.formal_attempt_id)
    assert resolution.outcome is ResolutionOutcome.NOT_APPLICABLE
    assert resolution.formal_attempt.state is FormalAttemptState.ACTIVE


async def test_resolution_of_an_unknown_attempt_is_a_404(container):
    with pytest.raises(Exception) as error:
        await container.services.results.resolve_by_id("fa-nope")
    assert error.value.status_code == 404
