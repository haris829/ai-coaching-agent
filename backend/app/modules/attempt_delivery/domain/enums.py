"""Enumerations owned by the UC-03 attempt domain.

``QuestionType`` and ``QuestionPresentation`` are **not** defined here: they are shared vocabulary
(:mod:`app.core.question_types`), because UC-02 authors the types and UC-01 configures the
presentation. They are re-exported so UC-03 code has one place to look for any enum.

``EnrolmentStatus`` is not here either: enrolment is the platform's, so its vocabulary and the
rule about which statuses may attempt live in :mod:`app.modules.identity.enums`. It is re-exported
for the same reason.

Everything else below is genuinely UC-03's: the attempt lifecycle, submission state, answer
provenance.

``StrEnum`` members compare equal to their string value, so they serialise directly into JSON and
into the ``String`` columns the schema uses. String columns with ``CHECK`` constraints are preferred
over native database enum types because they are portable between SQLite and PostgreSQL and can be
extended without a type migration.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.question_types import QuestionPresentation, QuestionType
from app.modules.identity.enums import (
    ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES,
    EnrolmentStatus,
)

#: Explicit, so the vocabulary re-exported from the shared kernel and the platform module is not
#: mistaken for an unused import and pruned.
__all__ = [
    "ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES",
    "LOCKED_ATTEMPT_STATUSES",
    "OPEN_ATTEMPT_STATUSES",
    "PRIMITIVE_QUESTION_TYPES",
    "AnswerSource",
    "AttemptStatus",
    "EnrolmentStatus",
    "QuestionPresentation",
    "QuestionType",
    "SubmissionReason",
    "SubmissionState",
]

#: Types that may appear as a scenario sub-question (no nesting).
PRIMITIVE_QUESTION_TYPES: frozenset[QuestionType] = frozenset(
    {
        QuestionType.SINGLE_CHOICE,
        QuestionType.TRUE_FALSE,
        QuestionType.MULTI_SELECT,
        QuestionType.DRAG_TO_ORDER,
    }
)


class AttemptStatus(StrEnum):
    """Lifecycle states of an attempt."""

    #: In progress. The only state in which answers may be modified.
    ACTIVE = "ACTIVE"
    #: The learner committed the attempt and it is locked, but the submission has
    #: not completed end to end (transient downstream failure). Retriable.
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    #: Terminal. Fully submitted and immutable.
    SUBMITTED = "SUBMITTED"


#: Statuses in which the learner can no longer change answers or flags.
LOCKED_ATTEMPT_STATUSES: frozenset[AttemptStatus] = frozenset(
    {AttemptStatus.SUBMISSION_PENDING, AttemptStatus.SUBMITTED}
)

#: Statuses that count as "open" for the one-open-attempt-per-quiz rule.
OPEN_ATTEMPT_STATUSES: frozenset[AttemptStatus] = frozenset(
    {AttemptStatus.ACTIVE, AttemptStatus.SUBMISSION_PENDING}
)


class SubmissionReason(StrEnum):
    """Why an attempt was committed."""

    #: The learner explicitly confirmed submission.
    LEARNER_CONFIRMED = "LEARNER_CONFIRMED"
    #: The server-authoritative timer reached zero.
    TIME_EXPIRED = "TIME_EXPIRED"


class SubmissionState(StrEnum):
    """State of a single submission record."""

    #: Committed locally, downstream hand-off outstanding. Retriable.
    PENDING = "PENDING"
    #: Complete. At most one per attempt, enforced by a partial unique index.
    SUBMITTED = "SUBMITTED"
    #: Permanently failed; the attempt was released back to ACTIVE.
    FAILED = "FAILED"


class AnswerSource(StrEnum):
    """Origin of a persisted answer, useful for support and analytics."""

    #: An explicit learner action.
    MANUAL = "MANUAL"
    #: The client's periodic background save.
    AUTOSAVE = "AUTOSAVE"
    #: Written by the server, e.g. when freezing answers at expiry.
    SYSTEM = "SYSTEM"
