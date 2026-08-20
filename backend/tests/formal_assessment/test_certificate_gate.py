"""The certificate gate (§11, §12, §20).

    formal assessment + passing result + assessor approval  ->  allowed
    anything less                                          ->  blocked

The most important file in the suite. Every state that is not an approved formal pass is asserted to be blocked,
because "we tested the happy path" is not an argument that a certificate cannot escape.
"""

from __future__ import annotations

import asyncio

import pytest

from app.modules.formal_assessment.domain.enums import (
    CertificateGateDecision,
    FormalAttemptState,
)
from app.modules.formal_assessment.domain.errors import (
    CertificateNotApprovedError,
    CertificateWorkflowFailedError,
)
from tests.formal_assessment.conftest import FormalFlow
from tests.formal_assessment.fakes import DEFAULT_ASSESSOR, DEFAULT_LEARNER

pytestmark = pytest.mark.anyio


async def _pending_review(flow: FormalFlow, container, passing):
    await flow.to_active()
    passing(flow.attempt_id)
    await flow.submit()
    review = await flow.review()
    assert review is not None
    return review


async def _approve(container, review_id: str):
    return await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review_id, decision="APPROVED"
    )


# ---------------------------------------------------------------------------
# Blocked
# ---------------------------------------------------------------------------


async def test_a_passing_result_alone_does_not_allow_a_certificate(flow: FormalFlow, container, passing, certificates):
    """The rule in one test: passing is not enough."""
    await _pending_review(flow, container, passing)
    eligibility = await container.services.certificates.check_eligibility_for_attempt(flow.attempt_id)
    assert eligibility.certificate_allowed is False
    assert eligibility.decision is CertificateGateDecision.BLOCKED
    assert eligibility.reason is not None and eligibility.reason.value == "PENDING_HUMAN_REVIEW"
    assert eligibility.retryable is True
    assert certificates.certificate_count == 0


async def test_every_pre_approval_state_is_blocked(flow: FormalFlow, container, passing):
    await flow.acknowledge()
    assert (await container.services.certificates.check_eligibility(flow.formal_attempt_id)).certificate_allowed is False

    await flow.confirm_identity()
    assert (await container.services.certificates.check_eligibility(flow.formal_attempt_id)).certificate_allowed is False

    await flow.start()
    assert (await container.services.certificates.check_eligibility(flow.formal_attempt_id)).certificate_allowed is False

    await flow.submit()  # no score arranged: SUBMITTED
    blocked = await container.services.certificates.check_eligibility(flow.formal_attempt_id)
    assert blocked.certificate_allowed is False
    assert blocked.reason is not None and blocked.reason.value == "RESULT_NOT_CALCULATED"


async def test_a_failed_formal_assessment_is_blocked_permanently(flow: FormalFlow, container, passing):
    await flow.to_active()
    passing(flow.attempt_id, passed=False, percentage=30.0)
    await flow.submit()
    eligibility = await container.services.certificates.check_eligibility(flow.formal_attempt_id)
    assert eligibility.certificate_allowed is False
    assert eligibility.reason is not None and eligibility.reason.value == "RESULT_NOT_PASSED"
    assert eligibility.retryable is False, "a fail will never become a certificate by waiting"


async def test_an_escalated_assessment_is_blocked_permanently(flow: FormalFlow, container, passing):
    review = await _pending_review(flow, container, passing)
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="REQUIRES_FURTHER_REVIEW"
    )
    eligibility = await container.services.certificates.check_eligibility(flow.formal_attempt_id)
    assert eligibility.certificate_allowed is False
    assert eligibility.reason is not None and eligibility.reason.value == "REQUIRES_FURTHER_REVIEW"
    assert eligibility.retryable is False


async def test_a_blocked_check_is_audited(flow: FormalFlow, container, passing, audit):
    await _pending_review(flow, container, passing)
    await container.services.certificates.check_eligibility_for_attempt(flow.attempt_id)
    assert "CERTIFICATE_BLOCKED" in audit.codes()
    fields = audit.fields_for("CERTIFICATE_BLOCKED")[-1]
    assert fields["reason"] == "PENDING_HUMAN_REVIEW"
    assert fields["state"] == "PENDING_REVIEW"


async def test_a_learners_own_read_is_not_audited_as_a_bypass(flow: FormalFlow, container, passing, audit):
    """A learner refreshing a page is not trying to get around anything."""
    await _pending_review(flow, container, passing)
    await container.services.certificates.check_eligibility_for_learner(
        learner_id=DEFAULT_LEARNER, formal_attempt_id=flow.formal_attempt_id
    )
    assert "CERTIFICATE_BLOCKED" not in audit.codes()


async def test_triggering_without_approval_is_refused(flow: FormalFlow, container, passing, certificates):
    """§11, §19: a direct call to the trigger reaches the same gate as the approval path."""
    await _pending_review(flow, container, passing)
    with pytest.raises(CertificateNotApprovedError) as error:
        await container.services.certificates.trigger_by_id(flow.formal_attempt_id)
    assert error.value.code == "CERTIFICATE_NOT_APPROVED"
    assert error.value.status_code == 403
    assert error.value.context["reason"] == "PENDING_HUMAN_REVIEW"
    assert certificates.certificate_count == 0


async def test_requiring_the_gate_raises_for_a_blocked_attempt(flow: FormalFlow, container, passing):
    await _pending_review(flow, container, passing)
    with pytest.raises(CertificateNotApprovedError):
        await container.services.certificates.require_allowed(attempt_id=flow.attempt_id)


# ---------------------------------------------------------------------------
# Not a formal assessment
# ---------------------------------------------------------------------------


async def test_an_ordinary_attempt_gets_no_opinion_from_uc09(container):
    """UC-09 adds a condition to formal assessments; it does not take over certificates."""
    eligibility = await container.services.certificates.check_eligibility_for_attempt("attempt-ordinary")
    assert eligibility.decision is CertificateGateDecision.NOT_FORMAL_ASSESSMENT
    assert eligibility.formal_assessment is False
    # Still false, so a caller that only reads this field fails safe rather than issuing on a "no opinion".
    assert eligibility.certificate_allowed is False


async def test_requiring_the_gate_for_an_ordinary_attempt_does_not_raise(container):
    eligibility = await container.services.certificates.require_allowed(attempt_id="attempt-ordinary")
    assert eligibility.decision is CertificateGateDecision.NOT_FORMAL_ASSESSMENT


# ---------------------------------------------------------------------------
# Allowed
# ---------------------------------------------------------------------------


async def test_approval_allows_the_certificate_and_triggers_the_workflow(
    flow: FormalFlow, container, passing, certificates, audit
):
    review = await _pending_review(flow, container, passing)
    outcome = await _approve(container, review.review_id)

    assert outcome.formal_attempt.state is FormalAttemptState.CERTIFICATE_ALLOWED
    assert outcome.formal_attempt.certificate_allowed is True
    assert certificates.certificate_count == 1
    assert "CERTIFICATE_WORKFLOW_TRIGGERED" in audit.codes()

    trigger = certificates.triggers[0]
    assert trigger.approved_by == DEFAULT_ASSESSOR
    assert trigger.review_id == review.review_id
    assert trigger.idempotency_key == f"formal-certificate:{flow.formal_attempt_id}"


async def test_the_gate_reports_the_approver(flow: FormalFlow, container, passing):
    review = await _pending_review(flow, container, passing)
    await _approve(container, review.review_id)
    eligibility = await container.services.certificates.check_eligibility_for_attempt(flow.attempt_id)
    assert eligibility.certificate_allowed is True
    assert eligibility.approved_by == DEFAULT_ASSESSOR
    assert eligibility.approved_at is not None


async def test_the_learner_is_notified_after_approval(flow: FormalFlow, container, passing, notifier, audit):
    review = await _pending_review(flow, container, passing)
    await _approve(container, review.review_id)
    assert notifier.events() == ["FORMAL_ASSESSMENT_APPROVED"]
    assert "LEARNER_NOTIFIED" in audit.codes()


async def test_a_learner_is_notified_when_the_assessment_is_escalated(flow: FormalFlow, container, passing, notifier):
    review = await _pending_review(flow, container, passing)
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="REQUIRES_FURTHER_REVIEW"
    )
    assert notifier.events() == ["FORMAL_ASSESSMENT_REQUIRES_FURTHER_REVIEW"]


# ---------------------------------------------------------------------------
# Idempotency and failure (§12, §20)
# ---------------------------------------------------------------------------


async def test_triggering_twice_produces_one_certificate(flow: FormalFlow, container, passing, certificates):
    """§20: two certificate requests must not generate two certificates."""
    review = await _pending_review(flow, container, passing)
    await _approve(container, review.review_id)
    replay = await container.services.certificates.trigger_by_id(flow.formal_attempt_id)
    assert replay.replayed is True
    assert replay.triggered is False
    assert certificates.certificate_count == 1


async def test_concurrent_triggers_produce_one_certificate(flow: FormalFlow, container, passing, certificates):
    review = await _pending_review(flow, container, passing)
    # Approve without the automatic trigger reaching the workflow, by breaking it first.
    certificates.break_workflow()
    await _approve(container, review.review_id)
    record = await flow.record()
    assert record.state is FormalAttemptState.APPROVED, "the approval stands despite the failure"

    certificates.repair()
    await asyncio.gather(
        container.services.certificates.trigger_by_id(flow.formal_attempt_id),
        container.services.certificates.trigger_by_id(flow.formal_attempt_id),
        return_exceptions=True,
    )
    assert certificates.certificate_count == 1
    final = await flow.record()
    assert final.state is FormalAttemptState.CERTIFICATE_ALLOWED


async def test_an_unreachable_workflow_leaves_the_approval_intact_and_retriable(
    flow: FormalFlow, container, passing, certificates
):
    """§12: the approval is persisted first, so a workflow outage costs a retry and nothing else."""
    review = await _pending_review(flow, container, passing)
    certificates.break_workflow()
    outcome = await _approve(container, review.review_id)

    assert outcome.review.approved is True
    assert outcome.formal_attempt.state is FormalAttemptState.APPROVED
    assert outcome.certificate is None
    assert certificates.certificate_count == 0

    certificates.repair()
    retried = await container.services.certificates.trigger_by_id(flow.formal_attempt_id)
    assert retried.triggered is True
    assert certificates.certificate_count == 1


async def test_a_refusing_workflow_is_a_retryable_failure_not_a_lost_approval(
    flow: FormalFlow, container, passing, certificates
):
    review = await _pending_review(flow, container, passing)
    certificates.accept = False
    await _approve(container, review.review_id)
    record = await flow.record()
    assert record.state is FormalAttemptState.APPROVED

    with pytest.raises(CertificateWorkflowFailedError) as error:
        await container.services.certificates.trigger_by_id(flow.formal_attempt_id)
    assert error.value.status_code == 503
    assert error.value.retryable is True


async def test_a_notification_failure_does_not_corrupt_the_approval(
    flow: FormalFlow, container, passing, notifier, certificates, audit
):
    """§12: notification failure must not corrupt the formal assessment state."""
    review = await _pending_review(flow, container, passing)
    notifier.break_notifier()
    outcome = await _approve(container, review.review_id)

    assert outcome.formal_attempt.state is FormalAttemptState.CERTIFICATE_ALLOWED
    assert outcome.notification_delivered is False
    assert certificates.certificate_count == 1
    assert "NOTIFICATION_FAILED" in audit.codes()

    stored = await flow.record()
    assert stored.state is FormalAttemptState.CERTIFICATE_ALLOWED


async def test_a_refused_notification_is_recorded_as_not_delivered(flow: FormalFlow, container, passing, notifier):
    review = await _pending_review(flow, container, passing)
    notifier.refuse = True
    outcome = await _approve(container, review.review_id)
    assert outcome.notification_delivered is False
    assert outcome.formal_attempt.certificate_allowed is True


async def test_the_gate_for_an_unknown_formal_attempt_is_a_404(container):
    with pytest.raises(Exception) as error:
        await container.services.certificates.check_eligibility("fa-nope")
    assert error.value.status_code == 404
