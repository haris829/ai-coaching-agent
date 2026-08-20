"""The certificate gate (§11) — the rule the rest of UC-09 exists to protect.

    formal assessment  +  passing result  +  assessor approval   ->  certificate allowed
    anything less                                                ->  BLOCKED

:func:`evaluate_certificate_eligibility` is a pure function of one formal attempt record. That
matters more than it looks: the gate has no I/O, no request context and no way to be told the
answer, so there is no argument a caller can pass that turns a pending review into an approval.
Every route to a certificate — the assessor decision path, an explicit trigger, a certificate
service asking before it generates — goes through this one function.

WHY A "NOT A FORMAL ASSESSMENT" ANSWER EXISTS
---------------------------------------------
UC-05 already decides certificates for ordinary quizzes and must keep doing so unchanged. So the
gate answers ``NOT_FORMAL_ASSESSMENT`` when there is no formal attempt record for the attempt, and
UC-05's existing rules apply. UC-09 adds a condition to formal assessments; it does not take over
certificates.

THE FAILURE DIRECTION
---------------------
Every uncertainty resolves to BLOCKED. No formal record for a quiz configured as formal, a result
that has not been calculated, a review that cannot be read — all of them block. A certificate issued
in error cannot be recalled from a learner who has already put it on a CV; a certificate delayed by
an hour can be issued an hour later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.formal_assessment.domain.attempt import FormalAttempt
from app.modules.formal_assessment.domain.enums import (
    CertificateBlockReason,
    CertificateGateDecision,
    FormalAttemptState,
)


@dataclass(frozen=True, slots=True)
class CertificateEligibility:
    """The gate's verdict, and everything a caller needs to act on it or explain it."""

    decision: CertificateGateDecision
    #: The one field a certificate service branches on.
    certificate_allowed: bool
    formal_assessment: bool
    reason: CertificateBlockReason | None = None
    message: str | None = None
    formal_attempt_id: str | None = None
    state: FormalAttemptState | None = None
    review_id: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    #: True when the block may lift on its own — the review is still pending. False when it never
    #: will without a new decision: a failed result, an escalated review.
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "certificate_allowed": self.certificate_allowed,
            "formal_assessment": self.formal_assessment,
            "reason": self.reason.value if self.reason else None,
            "message": self.message,
            "formal_attempt_id": self.formal_attempt_id,
            "state": self.state.value if self.state else None,
            "review_id": self.review_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "retryable": self.retryable,
            **({"details": dict(self.details)} if self.details else {}),
        }


_NOT_FORMAL = CertificateEligibility(
    decision=CertificateGateDecision.NOT_FORMAL_ASSESSMENT,
    certificate_allowed=False,
    formal_assessment=False,
    message=(
        "This attempt is not a formal assessment, so UC-09 imposes no additional certificate "
        "condition. The existing pass/fail and certificate rules apply unchanged."
    ),
)


def not_a_formal_assessment() -> CertificateEligibility:
    """The answer for an attempt UC-09 knows nothing about.

    ``certificate_allowed`` is False here as well, and deliberately: this function says "I have no
    opinion", not "go ahead". A caller reads ``decision`` to tell the two apart, so a caller that
    only checks ``certificate_allowed`` fails safe rather than issuing on the strength of a UC-09
    answer that was never about permission.
    """
    return _NOT_FORMAL


#: Why each blocked state is blocked, and whether waiting could change it.
_BLOCK_REASONS: dict[FormalAttemptState, tuple[CertificateBlockReason, str, bool]] = {
    FormalAttemptState.NOT_STARTED: (
        CertificateBlockReason.ATTEMPT_NOT_SUBMITTED,
        "The formal assessment has not been started.",
        True,
    ),
    FormalAttemptState.CONDITIONS_ACKNOWLEDGED: (
        CertificateBlockReason.ATTEMPT_NOT_SUBMITTED,
        "The formal assessment has not been started.",
        True,
    ),
    FormalAttemptState.IDENTITY_CONFIRMED: (
        CertificateBlockReason.ATTEMPT_NOT_SUBMITTED,
        "The formal assessment has not been started.",
        True,
    ),
    FormalAttemptState.ACTIVE: (
        CertificateBlockReason.ATTEMPT_NOT_SUBMITTED,
        "The formal assessment is still in progress.",
        True,
    ),
    FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS: (
        CertificateBlockReason.ATTEMPT_NOT_SUBMITTED,
        "The formal assessment is being submitted.",
        True,
    ),
    FormalAttemptState.SUBMITTED: (
        CertificateBlockReason.RESULT_NOT_CALCULATED,
        "The formal assessment result has not been calculated yet.",
        True,
    ),
    FormalAttemptState.RESULT_CALCULATED: (
        CertificateBlockReason.RESULT_NOT_CALCULATED,
        "The formal assessment result has not been finalised yet.",
        True,
    ),
    FormalAttemptState.FAILED: (
        CertificateBlockReason.RESULT_NOT_PASSED,
        "The formal assessment was not passed.",
        False,
    ),
    FormalAttemptState.PASSED: (
        CertificateBlockReason.PENDING_HUMAN_REVIEW,
        "The formal assessment has passed and is awaiting assessor review.",
        True,
    ),
    FormalAttemptState.PENDING_REVIEW: (
        CertificateBlockReason.PENDING_HUMAN_REVIEW,
        "The formal assessment is awaiting assessor review.",
        True,
    ),
    FormalAttemptState.REQUIRES_FURTHER_REVIEW: (
        CertificateBlockReason.REQUIRES_FURTHER_REVIEW,
        "The assessor referred this formal assessment for further review.",
        False,
    ),
}


def evaluate_certificate_eligibility(
    formal_attempt: FormalAttempt | None,
    *,
    approved_by: str | None = None,
    approved_at: str | None = None,
) -> CertificateEligibility:
    """Decide whether a certificate may be generated for this formal attempt (§11).

    ``approved_by`` / ``approved_at`` come from the review record when the caller has it. They are
    reported, never consulted: approval is established by the attempt's state, which only an
    authorised assessor's decision can have set.
    """
    if formal_attempt is None:
        return not_a_formal_assessment()

    if formal_attempt.certificate_allowed:
        return CertificateEligibility(
            decision=CertificateGateDecision.ALLOWED,
            certificate_allowed=True,
            formal_assessment=True,
            message="An authorised assessor has approved this formal assessment.",
            formal_attempt_id=formal_attempt.formal_attempt_id,
            state=formal_attempt.state,
            review_id=formal_attempt.review_id,
            approved_by=approved_by,
            approved_at=approved_at,
            details={
                "certificate_workflow_triggered_at": (
                    formal_attempt.certificate_workflow_triggered_at
                ),
            },
        )

    reason, message, retryable = _BLOCK_REASONS[formal_attempt.state]
    return CertificateEligibility(
        decision=CertificateGateDecision.BLOCKED,
        certificate_allowed=False,
        formal_assessment=True,
        reason=reason,
        message=message,
        formal_attempt_id=formal_attempt.formal_attempt_id,
        state=formal_attempt.state,
        review_id=formal_attempt.review_id,
        retryable=retryable,
    )
