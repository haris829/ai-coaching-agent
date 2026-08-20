"""The certificate gate and the certificate trigger (§11, §12, §19, §20).

    formal assessment  +  passing result  +  assessor approval   ->  workflow triggered
    anything less                                                ->  CERTIFICATE_NOT_APPROVED (403)

TWO ENTRY POINTS, ONE DECISION
------------------------------
``check_eligibility`` is the read: a certificate service — UC-05's, or any other caller — asks
whether it may generate, and gets an answer plus a reason. ``require_allowed`` is the same decision
as an assertion, for a caller that is about to act. Both delegate to the pure function in
``domain.certificate``, so there is exactly one place where "may a certificate exist for this
attempt?" is decided, and no argument a caller can pass that changes the answer.

**This is what makes the frontend irrelevant to the rule.** A direct call to a certificate endpoint
reaches the same gate as the assessor-approval path, because the gate reads persisted state — the
formal attempt's state, set only by an authorised assessor's decision — rather than anything in the
request.

WHY BLOCKING IS AUDITED
-----------------------
Every refusal emits ``CERTIFICATE_BLOCKED``. A blocked certificate is not an error in the system; it
is the system working. But it is also exactly the event a compliance question asks about later —
"did anyone try to issue this before it was approved?" — so it is recorded with the reason and the
state that produced it.

TRIGGERING IS IDEMPOTENT
------------------------
``trigger`` derives its idempotency key from the formal attempt, moves the attempt to
CERTIFICATE_ALLOWED under a compare-and-set, and treats an already-triggered attempt as a replay.
Two concurrent triggers therefore produce one certificate workflow call that matters; the workflow's
own key protects it a second time (§20).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.formal_assessment.domain.attempt import FormalAttempt
from app.modules.formal_assessment.domain.certificate import (
    CertificateEligibility,
    evaluate_certificate_eligibility,
    not_a_formal_assessment,
)
from app.modules.formal_assessment.domain.enums import FormalAttemptState, FormalAuditEvent
from app.modules.formal_assessment.domain.errors import (
    CertificateNotApprovedError,
    CertificateWorkflowFailedError,
    ConcurrentModificationError,
    FormalAttemptNotFoundError,
)
from app.modules.formal_assessment.domain.idempotency import certificate_key
from app.modules.formal_assessment.domain.review import FormalReview
from app.modules.formal_assessment.integration.audit import FormalAuditLog, safe_record
from app.modules.formal_assessment.integration.results import (
    CertificateTrigger,
    CertificateWorkflow,
)
from app.modules.formal_assessment.repositories.protocols import (
    FormalAttemptRepository,
    FormalReviewRepository,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CertificateOutcome:
    """What a trigger did."""

    formal_attempt: FormalAttempt
    eligibility: CertificateEligibility
    triggered: bool
    #: True when the attempt had already been triggered and this call did nothing new.
    replayed: bool = False
    reference: str | None = None
    workflow_status: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "formal_attempt_id": self.formal_attempt.formal_attempt_id,
            "state": self.formal_attempt.state.value,
            "triggered": self.triggered,
            "replayed": self.replayed,
            "reference": self.reference,
            "workflow_status": self.workflow_status,
            "certificate_workflow_triggered_at": (
                self.formal_attempt.certificate_workflow_triggered_at
            ),
            "eligibility": self.eligibility.as_dict(),
        }


class FormalCertificateService:
    def __init__(
        self,
        *,
        attempts: FormalAttemptRepository,
        reviews: FormalReviewRepository,
        workflow: CertificateWorkflow,
        audit: FormalAuditLog,
        clock: Clock,
    ) -> None:
        self._attempts = attempts
        self._reviews = reviews
        self._workflow = workflow
        self._audit = audit
        self._clock = clock

    # ------------------------------------------------------------------
    # The gate (§11)
    # ------------------------------------------------------------------

    async def check_eligibility_for_attempt(self, attempt_id: str) -> CertificateEligibility:
        """May a certificate be generated for this UC-03 attempt? (§11)

        The question a certificate service asks. An attempt with no formal record answers
        ``NOT_FORMAL_ASSESSMENT``, and UC-05's existing rules apply unchanged — UC-09 adds a
        condition to formal assessments rather than taking over certificates.
        """
        record = await self._attempts.get_by_attempt_id(attempt_id)
        if record is None:
            return not_a_formal_assessment()
        return await self._evaluate(record)

    async def check_eligibility(self, formal_attempt_id: str) -> CertificateEligibility:
        """The same decision, asked about a formal attempt."""
        record = await self._attempts.get(formal_attempt_id)
        if record is None:
            raise FormalAttemptNotFoundError(formal_attempt_id)
        return await self._evaluate(record)

    async def check_eligibility_for_learner(
        self, *, learner_id: str, formal_attempt_id: str
    ) -> CertificateEligibility:
        """The learner's own view of the gate, ownership-scoped (§17, §19)."""
        record = await self._attempts.get_for_learner(learner_id, formal_attempt_id)
        if record is None:
            raise FormalAttemptNotFoundError(formal_attempt_id)
        return await self._evaluate(record, audit_block=False)

    async def require_allowed(self, *, attempt_id: str) -> CertificateEligibility:
        """Assert that a certificate may be generated, or refuse (§11, §19).

        The guard a certificate endpoint calls before it does anything. A blocked formal assessment
        raises 403 ``CERTIFICATE_NOT_APPROVED`` — with the reason and the review id, so an operator
        can see what it is waiting for.
        """
        eligibility = await self.check_eligibility_for_attempt(attempt_id)
        if eligibility.decision.value == "NOT_FORMAL_ASSESSMENT":
            # UC-09 has no opinion. The caller's existing rules decide, and it is the caller's job
            # to
            # apply them — this method's contract is only about the formal condition.
            return eligibility
        if not eligibility.certificate_allowed:
            raise CertificateNotApprovedError(
                formal_attempt_id=eligibility.formal_attempt_id or "",
                state=eligibility.state.value if eligibility.state else "UNKNOWN",
                reason=eligibility.reason.value if eligibility.reason else "BLOCKED",
                review_id=eligibility.review_id,
            )
        return eligibility

    # ------------------------------------------------------------------
    # The trigger (§11, §12)
    # ------------------------------------------------------------------

    async def trigger(
        self,
        *,
        formal_attempt: FormalAttempt,
        review: FormalReview | None = None,
    ) -> CertificateOutcome:
        """Trigger the certificate workflow for an approved formal assessment (§11, §12, §20)."""
        eligibility = await self._evaluate(formal_attempt)
        if not eligibility.certificate_allowed:
            raise CertificateNotApprovedError(
                formal_attempt_id=formal_attempt.formal_attempt_id,
                state=formal_attempt.state.value,
                reason=eligibility.reason.value if eligibility.reason else "BLOCKED",
                review_id=formal_attempt.review_id,
            )

        if formal_attempt.state is FormalAttemptState.CERTIFICATE_ALLOWED:
            # Already triggered. Reported as a replay rather than sent again: two triggers must not
            # produce
            # two certificates.
            return CertificateOutcome(
                formal_attempt=formal_attempt,
                eligibility=eligibility,
                triggered=False,
                replayed=True,
                reference=formal_attempt.certificate_reference,
            )

        decision_review = review or (
            await self._reviews.get_by_formal_attempt(formal_attempt.formal_attempt_id)
        )
        if decision_review is None or decision_review.decision is None:  # pragma: no cover
            # Unreachable through the approval path: APPROVED is only set by a decision. Defensive,
            # because
            # a certificate must never be triggered without a named approver on the record.
            raise CertificateNotApprovedError(
                formal_attempt_id=formal_attempt.formal_attempt_id,
                state=formal_attempt.state.value,
                reason="APPROVAL_RECORD_MISSING",
                review_id=formal_attempt.review_id,
            )

        trigger = CertificateTrigger(
            formal_attempt_id=formal_attempt.formal_attempt_id,
            attempt_id=formal_attempt.attempt_id or "",
            learner_id=formal_attempt.learner_id,
            course_id=formal_attempt.course_id,
            quiz_id=formal_attempt.quiz_id,
            review_id=decision_review.review_id,
            approved_by=decision_review.decision.decided_by,
            approved_at=decision_review.decision.decided_at,
            idempotency_key=certificate_key(formal_attempt.formal_attempt_id),
            percentage=formal_attempt.result.percentage if formal_attempt.result else None,
            pass_mark=formal_attempt.result.pass_mark if formal_attempt.result else None,
            submitted_at=formal_attempt.submitted_at,
        )

        try:
            acknowledgement = await self._workflow.trigger(trigger)
        except Exception as error:  # noqa: BLE001 - mapped to one retryable failure for the caller
            raise CertificateWorkflowFailedError(
                "The certificate workflow could not be triggered. The approval stands and the "
                "trigger can be retried."
            ) from error

        if not acknowledgement.accepted:
            raise CertificateWorkflowFailedError(
                "The certificate workflow refused the request. The approval stands and the trigger "
                "can be retried."
            )

        now = to_iso(self._clock.now())
        try:
            stored = await self._attempts.save(
                formal_attempt.allow_certificate(
                    now=now, certificate_reference=acknowledgement.reference
                )
            )
        except ConcurrentModificationError:
            # A concurrent trigger won. The workflow key means both calls referred to the same
            # certificate,
            # so read the winner and report a replay rather than triggering again.
            fresh = await self._attempts.get(formal_attempt.formal_attempt_id)
            if fresh is None:  # pragma: no cover - there is no delete
                raise
            if fresh.state is FormalAttemptState.CERTIFICATE_ALLOWED:
                return CertificateOutcome(
                    formal_attempt=fresh,
                    eligibility=await self._evaluate(fresh),
                    triggered=False,
                    replayed=True,
                    reference=fresh.certificate_reference,
                    workflow_status=acknowledgement.status,
                )
            stored = await self._attempts.save(
                fresh.allow_certificate(now=now, certificate_reference=acknowledgement.reference)
            )

        await safe_record(
            self._audit,
            FormalAuditEvent.CERTIFICATE_WORKFLOW_TRIGGERED,
            formal_attempt_id=stored.formal_attempt_id,
            learner_id=stored.learner_id,
            course_id=stored.course_id,
            quiz_id=stored.quiz_id,
            attempt_id=stored.attempt_id,
            review_id=decision_review.review_id,
            approved_by=decision_review.decision.decided_by,
            approved_at=decision_review.decision.decided_at,
            certificate_reference=acknowledgement.reference,
            workflow_status=acknowledgement.status,
            already_requested=acknowledgement.already_requested,
        )

        return CertificateOutcome(
            formal_attempt=stored,
            eligibility=await self._evaluate(stored),
            triggered=True,
            replayed=acknowledgement.already_requested,
            reference=acknowledgement.reference,
            workflow_status=acknowledgement.status,
        )

    async def trigger_by_id(self, formal_attempt_id: str) -> CertificateOutcome:
        """Trigger by identifier — the retry path for an approval whose workflow call failed (§12).
        """
        record = await self._attempts.get(formal_attempt_id)
        if record is None:
            raise FormalAttemptNotFoundError(formal_attempt_id)
        return await self.trigger(formal_attempt=record)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _evaluate(
        self, record: FormalAttempt, *, audit_block: bool = True
    ) -> CertificateEligibility:
        """Evaluate the gate, reporting the approver and auditing a block.

        ``audit_block`` is False for the learner's own read of their status: a learner refreshing a
        page to see whether their certificate is ready is not an attempted bypass, and auditing it
        would bury the events that are.
        """
        review = None
        if record.review_id:
            review = await self._reviews.get(record.review_id)

        eligibility = evaluate_certificate_eligibility(
            record,
            approved_by=review.decision.decided_by if review and review.decision else None,
            approved_at=review.decision.decided_at if review and review.decision else None,
        )

        if audit_block and not eligibility.certificate_allowed:
            await safe_record(
                self._audit,
                FormalAuditEvent.CERTIFICATE_BLOCKED,
                formal_attempt_id=record.formal_attempt_id,
                learner_id=record.learner_id,
                course_id=record.course_id,
                quiz_id=record.quiz_id,
                attempt_id=record.attempt_id,
                review_id=record.review_id,
                state=record.state.value,
                reason=eligibility.reason.value if eligibility.reason else None,
                retryable=eligibility.retryable,
            )
        return eligibility
