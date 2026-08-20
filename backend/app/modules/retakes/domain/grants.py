"""Additional attempt grants (§11, §12).

An administrator grants extra attempts to **one learner on one course/quiz**. The grant is a
record in UC-08's own store; it is not, and cannot become, a change to the quiz configuration.

::

    Global maximum attempts (UC-01, immutable version)  =  2      ← untouched
    Learner A grants (UC-08)                            = +1
    Learner A entitlement                               =  3
    Learner B entitlement                               =  2      ← unaffected

That separation is structural rather than a matter of discipline: UC-08's UC-01 port is read-only
(``integration/uc01.py``), so there is no method anywhere in this module that could write a
``maximum_attempts``. The only way a grant could alter the course-wide maximum would be for
someone to add a write method to that port.

Scoping is by ``(learner_id, course_id, quiz_id)``. The course is part of the key because §11
describes the grant as being for a specific learner *and* course; the quiz is part of it because
a course may hold more than one quiz and an extra attempt at the module assessment is not an
extra attempt at everything.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.modules.retakes.domain.enums import GrantStatus


@dataclass(frozen=True, slots=True)
class AdditionalAttemptGrant:
    """One administrator grant of extra attempts.

    Frozen: a stored grant is replaced through :meth:`revoked`, never mutated in place, so a
    caller cannot quietly change the number of attempts it conferred after the fact. Revocation
    is a lifecycle transition that keeps the record and its history — grants are never deleted,
    because "who gave this learner a fourth attempt?" must stay answerable.
    """

    grant_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    #: How many extra attempts this grant confers. Always >= 1; validated on construction.
    additional_attempts: int
    #: The administrator identity resolved by the auth seam. Never client-supplied body data.
    granted_by: str
    #: Namespaced caller token. The uniqueness constraint that makes a retried grant safe.
    idempotency_key: str
    granted_at: str
    status: GrantStatus = GrantStatus.ACTIVE
    reason: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None

    @property
    def active(self) -> bool:
        return self.status is GrantStatus.ACTIVE

    @property
    def effective_attempts(self) -> int:
        """What this grant contributes to the allowance right now."""
        return self.additional_attempts if self.active else 0

    def revoked(self, *, at: str, by: str) -> AdditionalAttemptGrant:
        return replace(self, status=GrantStatus.REVOKED, revoked_at=at, revoked_by=by)

    def as_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "additional_attempts": self.additional_attempts,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
            "status": self.status.value,
            "reason": self.reason,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "idempotency_key": self.idempotency_key,
        }


def total_granted_attempts(grants: tuple[AdditionalAttemptGrant, ...] | list[Any]) -> int:
    """Sum the attempts conferred by the ACTIVE grants in a collection.

    Revoked grants contribute nothing, and a defensive ``max(0, …)`` means a corrupt stored value
    can only fail to help a learner rather than take an attempt away from them.
    """
    return sum(max(0, grant.effective_attempts) for grant in grants)
