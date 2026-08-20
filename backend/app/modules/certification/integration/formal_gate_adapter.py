"""UC-09, behind UC-05's ``CertificateGatePort``.

A single read of ``qs_formal_attempts``, run through UC-09's own
:func:`~app.modules.formal_assessment.domain.certificate.evaluate_certificate_eligibility` — the
same pure function UC-09's own system endpoint answers with. Reimplementing the decision here
would be a second certificate gate, and the two would eventually disagree about which state
releases a certificate.

**Synchronous, unlike UC-09's other seams.** UC-05's certification service is synchronous, and it
calls this from inside the transaction that is already deciding the outcome. Wrapping it in a
thread to satisfy an async signature would move the read out of that transaction for no benefit.

**An unreadable UC-09 raises.** ``ProviderUnavailableError`` propagates and the certificate stays
PENDING, which is the whole point of the obligation being durable: it is retried later rather than
issued on a guess. "We could not confirm an assessor approved this" must never become "issue it".
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.time import to_iso
from app.modules.certification.integration.formal_gate import (
    CertificateGateDecision,
    CertificateGateResult,
)
from app.modules.formal_assessment.domain.certificate import (
    evaluate_certificate_eligibility,
)
from app.modules.formal_assessment.domain.enums import (
    CertificateGateDecision as FormalGateDecision,
)
from app.modules.formal_assessment.models import FormalAttemptRow, FormalReviewRow
from app.modules.formal_assessment.repositories.sqlalchemy import to_domain_attempt

logger = get_logger(__name__)

#: UC-09's vocabulary mapped onto UC-05's. The two enums are separate on purpose — neither module
#: should have to import the other's domain to name its own answer — and this is the one place the
#: translation happens.
_DECISIONS = {
    FormalGateDecision.NOT_FORMAL_ASSESSMENT: CertificateGateDecision.NOT_FORMAL_ASSESSMENT,
    FormalGateDecision.ALLOWED: CertificateGateDecision.ALLOWED,
    FormalGateDecision.BLOCKED: CertificateGateDecision.BLOCKED,
}


class FormalCertificateGateAdapter:
    """``CertificateGatePort`` over UC-09's ``qs_formal_attempts``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def check_attempt(self, attempt_id: str) -> CertificateGateResult:
        try:
            row = self._session.scalar(
                select(FormalAttemptRow).where(FormalAttemptRow.attempt_id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise ProviderUnavailableError("uc09", cause=exc) from exc

        if row is None:
            # No formal record: an ordinary quiz attempt, and UC-05's rules apply unchanged. This
            # is the answer for the overwhelming majority of attempts.
            return CertificateGateResult(
                decision=CertificateGateDecision.NOT_FORMAL_ASSESSMENT
            )

        approved_by: str | None = None
        approved_at: str | None = None
        try:
            review = self._session.scalar(
                select(FormalReviewRow).where(FormalReviewRow.formal_attempt_id == row.id)
            )
        except SQLAlchemyError as exc:
            raise ProviderUnavailableError("uc09", cause=exc) from exc
        if review is not None and review.decided_by and review.decided_at:
            approved_by = review.decided_by
            approved_at = to_iso(review.decided_at)

        eligibility = evaluate_certificate_eligibility(
            to_domain_attempt(row), approved_by=approved_by, approved_at=approved_at
        )
        return CertificateGateResult(
            decision=_DECISIONS[eligibility.decision],
            reason=eligibility.reason.value if eligibility.reason else None,
            review_id=eligibility.review_id,
        )
