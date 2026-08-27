"""Pure UC-01 business rules.

No I/O, no framework, no adapter knowledge. Everything here is deterministic and
directly unit-testable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from . import messages
from .enums import (
    DependencyName,
    DependencyState,
    NaricAssessmentState,
    NaricLevelSource,
    SessionMode,
)
from .models import (
    DEFAULT_EXPLANATION_LEVEL,
    MAX_EXPLANATION_LEVEL,
    MIN_EXPLANATION_LEVEL,
    DependencyStatus,
    ModeAvailability,
    NaricAssessment,
    NaricResolution,
)

# --------------------------------------------------------------------------- #
# Session mode availability
# --------------------------------------------------------------------------- #


def evaluate_mode_availability(
    dependencies: Mapping[DependencyName, DependencyStatus],
) -> tuple[ModeAvailability, ...]:
    """Decide which of the three modes the user may open right now.

    Rules:

    * ``free-form`` is **always** available. No external dependency can disable it.
    * ``course-linked`` requires the Courses dependency to be reachable *and* to have at
      least one accessible course.
    * ``case-linked`` requires the Case dependency to be reachable *and* to have at
      least one accessible case file.

    A dependency failure therefore degrades one mode; it never disables the interface.
    """
    courses = dependencies.get(DependencyName.COURSES)
    cases = dependencies.get(DependencyName.CASES)

    return (
        ModeAvailability(mode=SessionMode.FREE_FORM, available=True, reason=None),
        _availability_for(
            SessionMode.COURSE_LINKED,
            courses,
            unavailable_reason=messages.COURSES_UNAVAILABLE,
            empty_reason=messages.COURSES_EMPTY,
        ),
        _availability_for(
            SessionMode.CASE_LINKED,
            cases,
            unavailable_reason=messages.CASES_UNAVAILABLE,
            empty_reason=messages.CASES_EMPTY,
        ),
    )


def _availability_for(
    mode: SessionMode,
    status: DependencyStatus | None,
    *,
    unavailable_reason: str,
    empty_reason: str,
) -> ModeAvailability:
    if status is None or status.state is DependencyState.UNAVAILABLE:
        return ModeAvailability(mode=mode, available=False, reason=unavailable_reason)
    if status.state is DependencyState.EMPTY:
        return ModeAvailability(mode=mode, available=False, reason=empty_reason)
    return ModeAvailability(mode=mode, available=True, reason=None)


def find_mode_availability(
    availability: Sequence[ModeAvailability], mode: SessionMode
) -> ModeAvailability:
    for entry in availability:
        if entry.mode is mode:
            return entry
    # Defensive: an unknown mode is never available.
    return ModeAvailability(mode=mode, available=False, reason=messages.GENERIC_DEGRADED_SESSION)


def available_modes(
    availability: Sequence[ModeAvailability],
) -> tuple[SessionMode, ...]:
    return tuple(entry.mode for entry in availability if entry.available)


# --------------------------------------------------------------------------- #
# NARIC fallback
# --------------------------------------------------------------------------- #


def _level_is_sane(level: int | None) -> bool:
    return (
        isinstance(level, int)
        and not isinstance(level, bool)
        and MIN_EXPLANATION_LEVEL <= level <= MAX_EXPLANATION_LEVEL
    )


def resolve_naric_level(
    assessment: NaricAssessment | None,
    status: DependencyStatus,
    *,
    continue_without_calibration: bool = False,
) -> NaricResolution:
    """Resolve the explanation level for a session.

    NARIC never blocks session creation. The level is either genuinely from NARIC, or it
    is the documented Level 5 default and is *labelled as such*.

    ``continue_without_calibration`` only changes the recorded source (so we can tell
    that the user was explicitly informed) and suppresses the repeated notice. It never
    changes whether the session can open.
    """
    if assessment is not None and assessment.state is NaricAssessmentState.COMPLETE:
        if _level_is_sane(assessment.level):
            return NaricResolution(
                level=int(assessment.level),  # type: ignore[arg-type]
                source=NaricLevelSource.NARIC,
                calibration_offer=False,
                notice=None,
            )
        # A "complete" assessment with a nonsensical level is treated as unusable data,
        # never silently trusted.
        return _fallback(
            notice=messages.NARIC_UNAVAILABLE_NOTICE,
            continue_without_calibration=continue_without_calibration,
        )

    if assessment is not None and assessment.state is NaricAssessmentState.CALIBRATING:
        return _fallback(
            notice=messages.NARIC_CALIBRATING_NOTICE,
            continue_without_calibration=continue_without_calibration,
        )

    if assessment is not None and assessment.state is NaricAssessmentState.INCOMPLETE:
        return _fallback(
            notice=messages.NARIC_INCOMPLETE_NOTICE,
            continue_without_calibration=continue_without_calibration,
        )

    # No assessment at all: unavailable or invalid upstream payload.
    notice = (
        messages.NARIC_INCOMPLETE_NOTICE
        if status.state is DependencyState.INCOMPLETE
        else messages.NARIC_UNAVAILABLE_NOTICE
    )
    return _fallback(
        notice=notice, continue_without_calibration=continue_without_calibration
    )


def _fallback(*, notice: str, continue_without_calibration: bool) -> NaricResolution:
    source = (
        NaricLevelSource.DEFAULT_USER_ACKNOWLEDGED
        if continue_without_calibration
        else NaricLevelSource.DEFAULT
    )
    return NaricResolution(
        level=DEFAULT_EXPLANATION_LEVEL,
        source=source,
        calibration_offer=not continue_without_calibration,
        notice=None if continue_without_calibration else notice,
    )


__all__ = [
    "available_modes",
    "evaluate_mode_availability",
    "find_mode_availability",
    "resolve_naric_level",
]
