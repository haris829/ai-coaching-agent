"""NARIC level normalisation and explanation-profile mapping.

Two platform rules are enforced here, in one place, so that every adapter gets
them identically:

1. A value mapping to no enum member is an **invalid response, not a level**.
   The ``LEVEL_5`` default is applied, the source is marked ``default``, the
   status is recorded as ``invalid``, and the event is logged. It is never
   coerced to a nearby level and never silently swallowed.
2. The explanation profile is a pure function of the level.

Adapters call :func:`resolve_naric_level` with whatever their upstream sent,
after translating any upstream-specific spelling into a candidate string. The
translation is the adapter concern; the ruling is this module concern.
"""

from __future__ import annotations

from typing import NamedTuple

from uc09_summary.domain.enums import (
    ExplanationProfile,
    NaricLevel,
    NaricLevelSource,
    SourceStatus,
)
from uc09_summary.logging_setup import get_logger

_log = get_logger(__name__)

#: Applied whenever a level cannot be established. Fixed by the platform contract.
DEFAULT_NARIC_LEVEL = NaricLevel.LEVEL_5

#: Level -> explanation profile. LEVEL_4 and LEVEL_6 are an assumption (A-002).
_PROFILE_BY_LEVEL: dict[NaricLevel, ExplanationProfile] = {
    NaricLevel.LEVEL_3: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_4: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_5: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_6: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_7: ExplanationProfile.ADVANCED,
    NaricLevel.LEVEL_7_PLUS: ExplanationProfile.ADVANCED,
}

#: Levels whose profile mapping the company has not confirmed. Recorded, not hidden.
ASSUMED_PROFILE_LEVELS = (NaricLevel.LEVEL_4, NaricLevel.LEVEL_6)


class NaricResolution(NamedTuple):
    """Outcome of normalising an upstream level value."""

    level: NaricLevel
    source: NaricLevelSource
    status: SourceStatus


def explanation_profile_for(level: NaricLevel) -> ExplanationProfile:
    """Return the explanation profile for a level. Total over the enum."""
    return _PROFILE_BY_LEVEL[level]


def profile_is_assumed(level: NaricLevel) -> bool:
    """True when the level to profile mapping is our assumption, not a company rule."""
    return level in ASSUMED_PROFILE_LEVELS


def resolve_naric_level(candidate: object, *, port: str = "session_provider") -> NaricResolution:
    """Normalise an upstream NARIC value onto the platform enum.

    Args:
        candidate: whatever the upstream sent, already translated by the adapter
            into a canonical candidate where the adapter knows how. ``None`` or
            an empty string means the upstream carried no value at all.
        port: logical port name, for the log record only.

    Returns:
        A :class:`NaricResolution`. The level is always a valid enum member.

    Rules:
        * a valid enum member or canonical string -> ``retrieved`` / ``available``
        * absent value -> ``LEVEL_5`` / ``default`` / ``empty``
        * present but unmappable -> ``LEVEL_5`` / ``default`` / ``invalid`` (+ log)
    """
    if isinstance(candidate, NaricLevel):
        return NaricResolution(candidate, NaricLevelSource.RETRIEVED, SourceStatus.AVAILABLE)

    if candidate is None or (isinstance(candidate, str) and not candidate.strip()):
        # Source answered, but carried nothing. Distinct from unavailable.
        return NaricResolution(
            DEFAULT_NARIC_LEVEL, NaricLevelSource.DEFAULT, SourceStatus.EMPTY
        )

    if isinstance(candidate, str):
        normalised = candidate.strip().lower()
        for member in NaricLevel:
            if normalised in (member.value, member.name.lower()):
                return NaricResolution(
                    member, NaricLevelSource.RETRIEVED, SourceStatus.AVAILABLE
                )

    # Present but maps to no enum member. This is an invalid response.
    _log.warning(
        "naric_level_invalid",
        port=port,
        candidate_type=type(candidate).__name__,
        applied_level=DEFAULT_NARIC_LEVEL.value,
        naric_level_source=NaricLevelSource.DEFAULT.value,
        naric_level_status=SourceStatus.INVALID.value,
    )
    return NaricResolution(
        DEFAULT_NARIC_LEVEL, NaricLevelSource.DEFAULT, SourceStatus.INVALID
    )
