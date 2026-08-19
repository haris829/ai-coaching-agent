"""Platform vocabulary.

Enrolment status is the company's vocabulary, not any one capability's, so it lives beside the
placeholder table rather than inside UC-03 — which merely *reads* it to decide eligibility.
"""

from __future__ import annotations

from enum import StrEnum


class EnrolmentStatus(StrEnum):
    """Course enrolment states relevant to attempt eligibility."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    SUSPENDED = "SUSPENDED"
    WITHDRAWN = "WITHDRAWN"


#: Enrolment statuses that permit starting an attempt.
#:
#: ``COMPLETED`` is included so a learner who has finished the course may still re-attempt a quiz;
#: ``SUSPENDED`` and ``WITHDRAWN`` are refused. This is a business rule the company may wish to
#: revisit, which is why it sits here as one named constant rather than inline in a service.
ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES: frozenset[EnrolmentStatus] = frozenset(
    {EnrolmentStatus.ACTIVE, EnrolmentStatus.COMPLETED}
)
