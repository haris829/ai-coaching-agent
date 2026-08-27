"""NARIC level handling and depth calibration."""

from __future__ import annotations

from ..domain.enums import (
    DEFAULT_NARIC_LEVEL,
    ExplanationProfile,
    NARIC_LEVEL_PROFILE,
    NaricLevel,
    NaricLevelSource,
    SourceStatus,
)
from ..domain.models import LearnerContext


def profile_for(level: NaricLevel) -> ExplanationProfile:
    return NARIC_LEVEL_PROFILE[level]


def coerce_level(raw: object) -> NaricLevel | None:
    """Map an upstream value onto the closed enum.

    Returns ``None`` when it maps to no member. That is an *invalid response*, not a level: the
    caller applies the default, marks the source ``default`` and records status ``invalid``.
    """
    if isinstance(raw, NaricLevel):
        return raw
    if isinstance(raw, str):
        try:
            return NaricLevel(raw.strip())
        except ValueError:
            return None
    return None


def normalise_context(context: LearnerContext) -> LearnerContext:
    """Guarantee the invariant: a defaulted level is never reported as retrieved."""
    if context.naric_level_source is NaricLevelSource.RETRIEVED and context.naric_level == DEFAULT_NARIC_LEVEL:
        return context
    if context.naric_level_source is NaricLevelSource.RETRIEVED:
        return context
    return context.model_copy(
        update={"naric_level": context.naric_level or DEFAULT_NARIC_LEVEL, "naric_level_source": NaricLevelSource.DEFAULT}
    )


def invalid_level_context(user_id: str) -> LearnerContext:
    """Applied when the upstream level maps to no enum member."""
    return LearnerContext(
        user_id=user_id,
        naric_level=DEFAULT_NARIC_LEVEL,
        naric_level_source=NaricLevelSource.DEFAULT,
        practice_area=None,
        source_status=SourceStatus.INVALID,
    )
