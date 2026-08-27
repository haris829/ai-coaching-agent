"""NARIC level -> explanation profile mapping, and safe level parsing.

The mapping is SPECIFIED by the company for levels 3, 5 and 7/7+.  Levels 4
and 6 are an assumption we record (A-PROFILE-4-6): Level 4 groups with Level 3
as ``basic`` and Level 6 groups with Level 5 as ``intermediate``.  Level 6 is
an undergraduate law degree, not Masters level, so it is deliberately NOT
mapped to ``advanced``.
"""

from __future__ import annotations

from .enums import ExplanationProfile, NaricLevel, NaricLevelSource, SourceStatus

# The default applied whenever a level cannot be established.  SPECIFIED by
# the company (section 7 of the brief).
DEFAULT_NARIC_LEVEL = NaricLevel.LEVEL_5

EXPLANATION_PROFILE_BY_LEVEL: dict[NaricLevel, ExplanationProfile] = {
    NaricLevel.LEVEL_3: ExplanationProfile.BASIC,        # SPECIFIED
    NaricLevel.LEVEL_4: ExplanationProfile.BASIC,        # ASSUMED (A-PROFILE-4-6)
    NaricLevel.LEVEL_5: ExplanationProfile.INTERMEDIATE,  # SPECIFIED
    NaricLevel.LEVEL_6: ExplanationProfile.INTERMEDIATE,  # ASSUMED (A-PROFILE-4-6)
    NaricLevel.LEVEL_7: ExplanationProfile.ADVANCED,      # SPECIFIED
    NaricLevel.LEVEL_7_PLUS: ExplanationProfile.ADVANCED,  # SPECIFIED
}


def explanation_profile_for(level: NaricLevel) -> ExplanationProfile:
    return EXPLANATION_PROFILE_BY_LEVEL[level]


def coerce_naric_level(
    raw: object,
) -> tuple[NaricLevel, NaricLevelSource, SourceStatus]:
    """Map an upstream value onto the platform enum, or fall back safely.

    A value mapping to no enum member is an **invalid response**, not a level:
    the default is applied, the source is marked ``default`` and the status is
    recorded as ``invalid``.  This function never raises and never invents a
    level -- both would be worse than a recorded fallback.

    Note that this is the *last* line of defence.  An adapter is expected to
    do its own mapping and raise ``ProviderInvalidResponse``; this exists so
    that a lax adapter cannot put a non-enum value into the domain.
    """
    if isinstance(raw, NaricLevel):
        return raw, NaricLevelSource.RETRIEVED, SourceStatus.AVAILABLE
    if raw is None:
        return DEFAULT_NARIC_LEVEL, NaricLevelSource.DEFAULT, SourceStatus.EMPTY
    try:
        return NaricLevel(str(raw)), NaricLevelSource.RETRIEVED, SourceStatus.AVAILABLE
    except ValueError:
        return DEFAULT_NARIC_LEVEL, NaricLevelSource.DEFAULT, SourceStatus.INVALID
