"""Fixed platform enumerations plus UC-07 owned derivation enumerations.

Anything marked SPECIFIED BY COMPANY is part of the fixed platform contract and
must not be extended locally. Anything marked ASSUMED BY US is a UC-07 modelling
decision and is recorded in docs/assumptions.md.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String enum with a stable ``str`` value (deterministic serialisation)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class NaricLevel(StrEnum):
    """SPECIFIED BY COMPANY. Never invent an integer NARIC scale."""

    LEVEL_3 = "LEVEL_3"
    LEVEL_4 = "LEVEL_4"
    LEVEL_5 = "LEVEL_5"
    LEVEL_6 = "LEVEL_6"
    LEVEL_7 = "LEVEL_7"
    LEVEL_7_PLUS = "LEVEL_7_PLUS"


class NaricLevelSource(StrEnum):
    """SPECIFIED BY COMPANY."""

    RETRIEVED = "retrieved"
    DEFAULT = "default"


class SourceStatus(StrEnum):
    """SPECIFIED BY COMPANY.

    ``EMPTY`` and ``UNAVAILABLE`` are different states and are never collapsed:
    empty means the source answered and holds nothing; unavailable means the
    source could not answer at all.
    """

    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class RatingState(StrEnum):
    """SPECIFIED BY COMPANY (InteractionRecord.rating_state)."""

    PENDING = "pending"
    RATED = "rated"


class Rating(StrEnum):
    """SPECIFIED BY COMPANY (FeedbackRecord.rating)."""

    UP = "up"
    DOWN = "down"


class ThresholdStatus(StrEnum):
    """ASSUMED BY US. Recommended values from the UC-07 brief."""

    BELOW_THRESHOLD = "below_threshold"
    AVAILABLE = "available"


class GapType(StrEnum):
    """ASSUMED BY US.

    ``STRUGGLE`` gaps are evidence-backed by interaction identifiers.
    ``UNEXPLORED`` gaps exist precisely because no interaction exists, so their
    evidence is the documented *absence* of interactions (see
    :class:`uc07.domain.models.GapEvidence`).
    """

    STRUGGLE = "struggle"
    UNEXPLORED = "unexplored"


class SignalKind(StrEnum):
    """ASSUMED BY US. Derivation signals; deterministic, never LLM-derived."""

    EXPLAIN_DIFFERENTLY = "explain_differently"
    FOLLOW_UP = "follow_up"
    LOW_RATING = "low_rating"
    UNEXPLORED_SPECIALITY = "unexplored_speciality"


#: Canonical signal ordering used for every emitted gap (determinism).
SIGNAL_ORDER: tuple[SignalKind, ...] = (
    SignalKind.EXPLAIN_DIFFERENTLY,
    SignalKind.FOLLOW_UP,
    SignalKind.LOW_RATING,
    SignalKind.UNEXPLORED_SPECIALITY,
)


class EvidenceBasis(StrEnum):
    """ASSUMED BY US. Why a gap's evidence looks the way it does."""

    INTERACTION_IDS = "interaction_ids"
    ZERO_INTERACTIONS_FOR_SPECIALITY_AREA = "zero_interactions_for_speciality_area"


class RecommendationStatus(StrEnum):
    """ASSUMED BY US. Report-level status of the recommendation section."""

    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    EMPTY = "empty"


class RecommendationType(StrEnum):
    """ASSUMED BY US."""

    COURSE = "course"
    LESSON = "lesson"


class DescriptionSource(StrEnum):
    """ASSUMED BY US. Proves a description was never generated."""

    REGISTRY = "registry"
    REGISTRY_DEFAULT = "registry_default"


class UnexploredAnalysisState(StrEnum):
    """ASSUMED BY US. Outcome of the speciality-coverage analysis."""

    PERFORMED = "performed"
    PERFORMED_PARTIAL = "performed_partial"
    NOT_PERFORMED_NO_SPECIALITY = "not_performed_no_speciality"
    NOT_PERFORMED_PROFILE_UNAVAILABLE = "not_performed_profile_unavailable"
    NOT_PERFORMED_PROFILE_INVALID = "not_performed_profile_invalid"


class NoticeSeverity(StrEnum):
    """ASSUMED BY US."""

    INFO = "info"
    WARNING = "warning"


class NoticeCode(StrEnum):
    """ASSUMED BY US. Stable machine-readable caveat codes."""

    RECOMMENDATIONS_TEMPORARILY_UNAVAILABLE = "recommendations_temporarily_unavailable"
    RECOMMENDATIONS_PARTIAL = "recommendations_partial"
    RATING_SIGNAL_UNAVAILABLE = "rating_signal_unavailable"
    RATING_SIGNAL_PARTIAL = "rating_signal_partial"
    RATING_SIGNAL_NO_RATINGS = "rating_signal_no_ratings"
    RATING_SIGNAL_INVALID = "rating_signal_invalid"
    SPECIALITY_ANALYSIS_UNAVAILABLE = "speciality_analysis_unavailable"
    SPECIALITY_ANALYSIS_INVALID = "speciality_analysis_invalid"
    SPECIALITY_ANALYSIS_PARTIAL = "speciality_analysis_partial"
    SPECIALITY_ANALYSIS_NOT_POSSIBLE_NO_SPECIALITY = (
        "speciality_analysis_not_possible_no_speciality"
    )
    INSUFFICIENT_TOPIC_DIVERSITY = "insufficient_topic_diversity"
    INTERACTION_SOURCE_PARTIAL = "interaction_source_partial"
