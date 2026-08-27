"""Unexplored-speciality analysis.

Speciality areas are read from the learner profile and compared against the topic
tags actually present in the learner's interaction history. Comparison is exact
string equality: speciality areas are assumed to be drawn from the same
vocabulary as ``topic_tag`` (docs/assumptions.md A-15). UC-07 never fuzzy-matches,
never infers a speciality from history, and never invents one.
"""

from __future__ import annotations

from dataclasses import dataclass

from uc07.domain.enums import SourceStatus, UnexploredAnalysisState
from uc07.domain.models import LearnerProfile, UnexploredAnalysis


@dataclass(frozen=True, slots=True)
class ProfileLoad:
    """Outcome of reading the profile source, status preserved verbatim."""

    status: SourceStatus
    profile: LearnerProfile | None

    @classmethod
    def loaded(cls, profile: LearnerProfile) -> "ProfileLoad":
        return cls(status=profile.speciality_status, profile=profile)

    @classmethod
    def failed(cls, status: SourceStatus) -> "ProfileLoad":
        return cls(status=status, profile=None)


@dataclass(frozen=True, slots=True)
class UnexploredOutcome:
    """Analysis result plus the speciality areas with zero interactions."""

    analysis: UnexploredAnalysis
    unexplored_areas: tuple[str, ...]


_EXPLANATIONS = {
    UnexploredAnalysisState.PERFORMED: (
        "Speciality areas were compared against the topic tags present in the "
        "learner's interaction history."
    ),
    UnexploredAnalysisState.PERFORMED_PARTIAL: (
        "Speciality data was partial: the areas that were retrieved were compared "
        "against interaction history, but unexplored-speciality analysis may be "
        "incomplete because further speciality areas may exist upstream."
    ),
    UnexploredAnalysisState.NOT_PERFORMED_NO_SPECIALITY: (
        "Unexplored-speciality analysis could not be performed: the learner has no "
        "speciality areas set. No speciality was inferred from question history."
    ),
    UnexploredAnalysisState.NOT_PERFORMED_PROFILE_UNAVAILABLE: (
        "Unexplored-speciality analysis was unavailable: the learner profile source "
        "could not be read. No speciality areas were invented; evidence-based "
        "struggle analysis was performed regardless."
    ),
    UnexploredAnalysisState.NOT_PERFORMED_PROFILE_INVALID: (
        "Unexplored-speciality analysis could not be performed: the learner profile "
        "source returned data that does not satisfy the platform contract. No "
        "speciality areas were invented."
    ),
}


def analyse_unexplored(
    load: ProfileLoad, history_topic_tags: tuple[str, ...]
) -> UnexploredOutcome:
    """Classify speciality areas with zero interactions as unexplored gaps."""
    covered = set(history_topic_tags)

    if load.profile is None:
        state = (
            UnexploredAnalysisState.NOT_PERFORMED_PROFILE_INVALID
            if load.status is SourceStatus.INVALID
            else UnexploredAnalysisState.NOT_PERFORMED_PROFILE_UNAVAILABLE
        )
        return UnexploredOutcome(
            analysis=UnexploredAnalysis(
                state=state,
                speciality_status=load.status,
                speciality_areas_considered=0,
                unexplored_areas_found=0,
                may_be_incomplete=True,
                explanation=_EXPLANATIONS[state],
            ),
            unexplored_areas=(),
        )

    speciality_status = load.profile.speciality_status
    areas = load.profile.speciality_areas

    if speciality_status in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
        state = (
            UnexploredAnalysisState.NOT_PERFORMED_PROFILE_INVALID
            if speciality_status is SourceStatus.INVALID
            else UnexploredAnalysisState.NOT_PERFORMED_PROFILE_UNAVAILABLE
        )
        return UnexploredOutcome(
            analysis=UnexploredAnalysis(
                state=state,
                speciality_status=speciality_status,
                speciality_areas_considered=0,
                unexplored_areas_found=0,
                may_be_incomplete=True,
                explanation=_EXPLANATIONS[state],
            ),
            unexplored_areas=(),
        )

    if speciality_status is SourceStatus.EMPTY:
        state = UnexploredAnalysisState.NOT_PERFORMED_NO_SPECIALITY
        return UnexploredOutcome(
            analysis=UnexploredAnalysis(
                state=state,
                speciality_status=speciality_status,
                speciality_areas_considered=0,
                unexplored_areas_found=0,
                may_be_incomplete=False,
                explanation=_EXPLANATIONS[state],
            ),
            unexplored_areas=(),
        )

    unexplored = tuple(sorted(area for area in areas if area not in covered))
    partial = speciality_status is SourceStatus.PARTIAL
    state = (
        UnexploredAnalysisState.PERFORMED_PARTIAL
        if partial
        else UnexploredAnalysisState.PERFORMED
    )
    return UnexploredOutcome(
        analysis=UnexploredAnalysis(
            state=state,
            speciality_status=speciality_status,
            speciality_areas_considered=len(areas),
            unexplored_areas_found=len(unexplored),
            may_be_incomplete=partial,
            explanation=_EXPLANATIONS[state],
        ),
        unexplored_areas=unexplored,
    )
