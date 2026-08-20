"""UC-04 (Scoring), UC-05 (Pass/Fail and certificates) — the contracts UC-09 consumes (§8, §11,
§12).

**UC-09 contains no scoring engine and no pass-mark comparison.** It reads a confirmed score from
UC-04 and a determined pass/fail result from UC-05, copies them onto the formal attempt, and then
does the one thing that is actually its own: it holds a pass at PENDING_REVIEW instead of letting a
certificate follow from it.

    UC-04 confirmed score  ->  UC-05 pass/fail  ->  UC-09 records it  ->  PENDING_REVIEW
                                                                              |
                                                          assessor approval --+--> certificate

THE ONE INVERSION WORTH NOTICING
--------------------------------
Every other port here is UC-09 reading from a module upstream of it. :class:`CertificateWorkflow` is
UC-09 *calling* UC-05's certificate workflow — but only ever after an approval, and the trigger
carries the approval on its face. Meanwhile UC-05's certificate service asks UC-09's gate before it
generates anything, which is the check that makes the rule hold for callers UC-09 never sees. The
two directions are not a cycle: one is "may I?", the other is "you may now", and both are explicit.

WHY THE PASS/FAIL RESULT IS *COPIED* ONTO THE FORMAL ATTEMPT
-----------------------------------------------------------
A review may happen days later. An assessor must see the result the learner actually got, at the
percentage it was calculated to, against the pass mark that was in force — not a re-read that could
have moved. UC-05's result is immutable once determined, so the copy cannot disagree with it; it
exists so the review payload is a single read and so the formal record is self-describing.

``PENDING`` results are not recorded. UC-05 uses PENDING for "no safe decision is possible yet", and
a formal attempt whose result is not yet decided simply stays SUBMITTED and is resolved later.
Recording a PENDING result as a formal result would create a formal attempt that had a result but no
outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: UC-04's score statuses and UC-05's result statuses, as strings. Not redeclared as enums here —
#: they
#: belong to those modules.
SCORE_STATUS_CONFIRMED = "CONFIRMED"
RESULT_STATUS_PENDING = "PENDING"
RESULT_STATUS_PASSED = "PASSED"
RESULT_STATUS_FAILED = "FAILED"

#: The result statuses UC-05 considers decided. Only these produce a formal result.
DETERMINED_RESULT_STATUSES: frozenset[str] = frozenset(
    {RESULT_STATUS_PASSED, RESULT_STATUS_FAILED}
)


@dataclass(frozen=True, slots=True)
class AttemptScore:
    """UC-04's confirmed score for an attempt."""

    attempt_id: str
    status: str
    total_marks: float | None = None
    maximum_marks: float | None = None
    percentage: float | None = None
    scored_at: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.status == SCORE_STATUS_CONFIRMED

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "status": self.status,
            "total_marks": self.total_marks,
            "maximum_marks": self.maximum_marks,
            "percentage": self.percentage,
            "scored_at": self.scored_at,
        }


@dataclass(frozen=True, slots=True)
class PassFailResult:
    """UC-05's pass/fail decision for an attempt."""

    attempt_id: str
    status: str
    result_id: str | None = None
    percentage: float | None = None
    pass_mark: float | None = None
    determined_at: str | None = None

    @property
    def determined(self) -> bool:
        return self.status in DETERMINED_RESULT_STATUSES

    @property
    def passed(self) -> bool:
        return self.status == RESULT_STATUS_PASSED

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "status": self.status,
            "result_id": self.result_id,
            "percentage": self.percentage,
            "pass_mark": self.pass_mark,
            "determined_at": self.determined_at,
        }


@runtime_checkable
class ScoringResultProvider(Protocol):
    """UC-04. Read-only: UC-09 never scores and never re-scores."""

    async def get_score(self, attempt_id: str) -> AttemptScore | None: ...


@runtime_checkable
class PassFailResultProvider(Protocol):
    """UC-05's pass/fail decision. Read-only: UC-09 never decides a pass."""

    async def get_result(self, attempt_id: str) -> PassFailResult | None: ...


@dataclass(frozen=True, slots=True)
class CertificateTrigger:
    """The request UC-09 sends when an approved formal assessment may have its certificate (§11,
    §12).

    Carries the approval on its face — who approved it, when, and the review it came from — so the
    certificate workflow, an operator reading its logs, and the certificate record itself can all
    show why the certificate was permitted. A trigger without an ``approved_by`` is not a thing this
    module can construct: the service builds it from the decision record.
    """

    formal_attempt_id: str
    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    review_id: str
    approved_by: str
    approved_at: str
    #: Derived from the formal attempt, so a retried trigger cannot produce a second certificate.
    idempotency_key: str = ""
    percentage: float | None = None
    pass_mark: float | None = None
    submitted_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "formal_attempt_id": self.formal_attempt_id,
            "attempt_id": self.attempt_id,
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "review_id": self.review_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "idempotency_key": self.idempotency_key,
            "percentage": self.percentage,
            "pass_mark": self.pass_mark,
            "submitted_at": self.submitted_at,
        }


@dataclass(frozen=True, slots=True)
class CertificateAcknowledgement:
    """What the certificate workflow said when it accepted the trigger.

    ``already_requested`` is how an idempotent provider reports "I have this one already" — which
    UC-09
    treats as success, because it is.
    """

    accepted: bool
    reference: str | None = None
    status: str | None = None
    already_requested: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reference": self.reference,
            "status": self.status,
            "already_requested": self.already_requested,
        }


@runtime_checkable
class CertificateWorkflow(Protocol):
    """UC-05's existing certificate workflow. **UC-09 builds no certificate system of its own.**"""

    async def trigger(self, request: CertificateTrigger) -> CertificateAcknowledgement:
        """Start certificate generation for an approved formal assessment.

        Must be idempotent on ``request.idempotency_key``: two triggers must not produce two
        certificates (§20). Raise for a transient failure — the approval stands and the trigger is
        retriable — rather than returning ``accepted=False``, which means the workflow refused.
        """
        ...
