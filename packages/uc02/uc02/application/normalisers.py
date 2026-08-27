"""Record -> context normalisation, one function per source.

This module implements the failure and default matrix from the UC-02 scope
section 7, and the source-status vocabulary from section 8. It is pure: no I/O,
no clock, no logging. That makes the whole matrix unit-testable without touching
the assembly service or a provider.

Two rules run through all of it:

1. Never fabricate data to fill a gap. A missing speciality yields general legal
   explanations plus a recorded status, never a guessed practice area.
2. ``empty`` (the learner genuinely has nothing) and ``unavailable`` (the source
   is down) are different statuses and are never conflated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from uc02.domain.errors import ProviderError
from uc02.domain.models.context import (
    DEFAULT_NARIC_LEVEL,
    CourseContext,
    CoursesContext,
    LegalContext,
    NaricContext,
    QuestionHistoryContext,
    QuestionHistoryItem,
)
from uc02.domain.models.enums import (
    ErrorCategory,
    ExplanationDomain,
    LevelSource,
    SourceStatus,
)
from uc02.domain.models.provider_records import (
    CoursesRecord,
    LegalProfileRecord,
    NaricRecord,
    QuestionRecord,
)

#: How much of a question we keep server-side. Never returned by the API.
QUESTION_EXCERPT_CHARS = 160

T = TypeVar("T")


@dataclass(frozen=True)
class Normalised(Generic[T]):
    """A normalised context fragment plus how we got there."""

    value: T
    status: SourceStatus
    error_category: ErrorCategory = ErrorCategory.NONE
    fallback_applied: bool = False
    #: Short, non-sensitive descriptions of each default applied. Safe to log.
    fallbacks: tuple[str, ...] = field(default_factory=tuple)


def status_for_error(error: ProviderError) -> SourceStatus:
    """Map an error category to a source status.

    Transport-shaped failures are ``unavailable``; contract-shaped failures
    (a response we cannot parse, or an adapter raising outside its contract)
    are ``invalid``. Either way defaults are applied and assembly continues.
    """
    if error.category in (ErrorCategory.INVALID_RESPONSE, ErrorCategory.UNEXPECTED):
        return SourceStatus.INVALID
    return SourceStatus.UNAVAILABLE


# --------------------------------------------------------------------------
# NARIC
# --------------------------------------------------------------------------
def normalise_naric(
    record: NaricRecord | None, error: ProviderError | None = None
) -> Normalised[NaricContext]:
    if error is not None:
        return Normalised(
            value=NaricContext(level=DEFAULT_NARIC_LEVEL, level_source=LevelSource.DEFAULT),
            status=status_for_error(error),
            error_category=error.category,
            fallback_applied=True,
            fallbacks=(f"naric.level defaulted to {DEFAULT_NARIC_LEVEL}",),
        )
    if record is None or record.level is None:
        # NARIC answered but holds no qualification for this learner.
        return Normalised(
            value=NaricContext(
                level=DEFAULT_NARIC_LEVEL,
                level_source=LevelSource.DEFAULT,
                raw_level_label=record.raw_level_label if record else None,
            ),
            status=SourceStatus.EMPTY,
            fallback_applied=True,
            fallbacks=(f"naric.level defaulted to {DEFAULT_NARIC_LEVEL} (no qualification held)",),
        )
    return Normalised(
        value=NaricContext(
            level=record.level,
            level_source=LevelSource.RETRIEVED,
            raw_level_label=record.raw_level_label,
        ),
        status=SourceStatus.AVAILABLE,
    )


# --------------------------------------------------------------------------
# Courses
# --------------------------------------------------------------------------
def normalise_courses(
    record: CoursesRecord | None, error: ProviderError | None = None
) -> Normalised[CoursesContext]:
    if error is not None:
        return Normalised(
            value=CoursesContext(enrolments=()),
            status=status_for_error(error),
            error_category=error.category,
            fallback_applied=True,
            fallbacks=("courses.enrolments defaulted to []",),
        )
    enrolments = tuple(
        CourseContext(
            course_id=item.course_id,
            course_name=item.course_name,
            completion_percentage=item.completion_percentage,
            last_accessed_lesson_id=item.last_accessed_lesson_id,
            last_accessed_lesson_name=item.last_accessed_lesson_name,
        )
        for item in (record.enrolments if record else ())
    )
    if not enrolments:
        # Genuinely no enrolments. Not a failure, not a fallback.
        return Normalised(value=CoursesContext(enrolments=()), status=SourceStatus.EMPTY)
    incomplete = any(e.last_accessed_lesson_id is None for e in enrolments)
    return Normalised(
        value=CoursesContext(enrolments=enrolments),
        status=SourceStatus.PARTIAL if incomplete else SourceStatus.AVAILABLE,
    )


# --------------------------------------------------------------------------
# Legal Foot Prints
# --------------------------------------------------------------------------
def normalise_legal(
    record: LegalProfileRecord | None, error: ProviderError | None = None
) -> Normalised[LegalContext]:
    if error is not None:
        return Normalised(
            value=LegalContext(explanation_domain=ExplanationDomain.GENERAL_LEGAL),
            status=status_for_error(error),
            error_category=error.category,
            fallback_applied=True,
            fallbacks=(
                "legal_profile.speciality_areas defaulted to []",
                "legal_profile.case_type_preferences defaulted to []",
                "legal_profile.practice_area defaulted to None",
                "legal_profile.explanation_domain defaulted to general_legal",
            ),
        )
    record = record or LegalProfileRecord()
    has_speciality = bool(record.speciality_areas)
    domain = ExplanationDomain.SPECIALITY if has_speciality else ExplanationDomain.GENERAL_LEGAL
    value = LegalContext(
        speciality_areas=record.speciality_areas,
        case_type_preferences=record.case_type_preferences,
        practice_area=record.practice_area,
        explanation_domain=domain,
    )
    if not has_speciality and not record.case_type_preferences and record.practice_area is None:
        return Normalised(value=value, status=SourceStatus.EMPTY)
    if not has_speciality or record.practice_area is None:
        return Normalised(value=value, status=SourceStatus.PARTIAL)
    return Normalised(value=value, status=SourceStatus.AVAILABLE)


# --------------------------------------------------------------------------
# Question history
# --------------------------------------------------------------------------
def normalise_history(
    records: Sequence[object] | None,
    error: ProviderError | None = None,
    *,
    limit: int,
) -> Normalised[QuestionHistoryContext]:
    if error is not None:
        return Normalised(
            value=QuestionHistoryContext(),
            status=status_for_error(error),
            error_category=error.category,
            fallback_applied=True,
            fallbacks=("question_history.items defaulted to []",),
        )
    supplied = list(records or [])
    # An adapter may hand back a row we cannot parse. Drop it, count it, carry on.
    valid = [r for r in supplied if isinstance(r, QuestionRecord)]
    dropped = len(supplied) - len(valid)

    valid.sort(key=lambda r: r.asked_at, reverse=True)
    truncated = len(valid) > limit
    kept = valid[:limit]
    items = tuple(
        QuestionHistoryItem(
            question_id=r.question_id,
            session_id=r.session_id,
            asked_at=r.asked_at,
            topic_tag=r.topic_tag,
            text_excerpt=r.text[:QUESTION_EXCERPT_CHARS],
        )
        for r in kept
    )
    value = QuestionHistoryContext(
        items=items, truncated=truncated, dropped_malformed_count=dropped
    )

    if not supplied:
        # The learner has genuinely asked nothing before. Not a failure.
        return Normalised(value=value, status=SourceStatus.EMPTY)
    if dropped and not valid:
        return Normalised(
            value=value,
            status=SourceStatus.INVALID,
            error_category=ErrorCategory.INVALID_RESPONSE,
        )
    if dropped:
        return Normalised(
            value=value,
            status=SourceStatus.PARTIAL,
            error_category=ErrorCategory.INVALID_RESPONSE,
        )
    return Normalised(value=value, status=SourceStatus.AVAILABLE)
