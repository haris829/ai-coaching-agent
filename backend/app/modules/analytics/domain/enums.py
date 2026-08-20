"""Enumerations shared by the repository contract and the API contract."""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AssessmentType",
    "AttemptStatus",
    "ReportingQuestionType",
    "DataState",
    "FlagStatus",
    "FlagReason",
    "ReviewActionType",
    "AnalyticsScope",
    "QuestionSortField",
    "SortDirection",
]


class AssessmentType(StrEnum):
    """Assessment families recognised by the platform (spec section 9)."""

    STANDARD_QUIZ = "STANDARD_QUIZ"
    FORMAL_ASSESSMENT = "FORMAL_ASSESSMENT"


class AttemptStatus(StrEnum):
    """Lifecycle state of an attempt, as reported by the assessment system."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


class ReportingQuestionType(StrEnum):
    """UC-10's *reporting* vocabulary for question shapes — not the system's question types.

    Deliberately distinct from ``app.core.question_types.QuestionType``, which is the shared kernel
    UC-01 and UC-02 agree on and which has exactly five members. This one is broader and more
    generic because UC-10 was written to report on any assessment system's data, and it keeps that
    breadth: a dashboard that grouped by the kernel's five names would be a dashboard welded to
    this one system.

    The two are bridged in ``integration/question_types.py``, and the exact system name travels in
    ``question_type_label`` so nothing a reader sees is ever the generic mapping. It was originally
    called ``QuestionType`` too; renamed during the merge because two classes with one name and
    different members is how a translation gets skipped.
    """

    """Question formats.

    ``OTHER`` is a deliberate escape hatch: the external system owns the
    question catalogue and may introduce formats UC-10 has never heard of. An
    unknown value is normalised to ``OTHER`` rather than failing the whole
    aggregation, and the provider's original label is preserved on
    :class:`~app.modules.analytics.domain.records.QuestionMetadata.question_type_label`.
    """

    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    MULTI_SELECT = "MULTI_SELECT"
    TRUE_FALSE = "TRUE_FALSE"
    SHORT_ANSWER = "SHORT_ANSWER"
    NUMERIC = "NUMERIC"
    MATCHING = "MATCHING"
    OTHER = "OTHER"

    @classmethod
    def _missing_(cls, value: object) -> ReportingQuestionType:
        if isinstance(value, str):
            candidate = value.strip().upper().replace("-", "_").replace(" ", "_")
            for member in cls:
                if member.value == candidate:
                    return member
        return cls.OTHER


class DataState(StrEnum):
    """Whether a reported figure rests on any data at all (spec section 12).

    ``OK``
        Records were found; the numbers are real computed values. A metric of
        ``0`` under this state means genuinely zero.
    ``NO_ATTEMPTS``
        No records matched the filters. Metrics are ``null``, never zero, and
        never fabricated.
    """

    OK = "OK"
    NO_ATTEMPTS = "NO_ATTEMPTS"


class FlagStatus(StrEnum):
    """Content-review flag lifecycle.

    A flag is created by threshold evaluation and only ever leaves ``FLAGGED``
    through an explicit review action (spec section 18).
    """

    FLAGGED = "FLAGGED"
    RESOLVED = "RESOLVED"
    RETIRED = "RETIRED"


class FlagReason(StrEnum):
    """Why a flag record exists.

    ``ADMINISTRATIVE_ACTION`` covers records created by a review decision rather
    than by measurement - retiring a question that was never flagged, for
    instance. Such a record carries no wrong-answer rate, because none was
    measured; the measurement fields are null rather than zero.
    """

    WRONG_ANSWER_RATE_EXCEEDED = "WRONG_ANSWER_RATE_EXCEEDED"
    ADMINISTRATIVE_ACTION = "ADMINISTRATIVE_ACTION"


class ReviewActionType(StrEnum):
    """Decisions an administrator can record against a question (spec 11)."""

    NO_CHANGE = "NO_CHANGE"
    QUESTION_UPDATED = "QUESTION_UPDATED"
    QUESTION_RETIRED = "QUESTION_RETIRED"

    def resulting_flag_status(self) -> FlagStatus:
        """Flag state a question moves to once this action is recorded."""
        if self is ReviewActionType.QUESTION_RETIRED:
            return FlagStatus.RETIRED
        return FlagStatus.RESOLVED


class AnalyticsScope(StrEnum):
    """Aggregation breadth of a response."""

    PLATFORM = "PLATFORM"
    COURSE = "COURSE"


class QuestionSortField(StrEnum):
    """Ordering keys for question analytics listings."""

    QUESTION_ID = "question_id"
    ACCURACY = "accuracy_percentage"
    WRONG_ANSWER_RATE = "wrong_answer_rate"
    ATTEMPT_COUNT = "attempt_count"
    AVERAGE_TIME = "average_time_seconds"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"
