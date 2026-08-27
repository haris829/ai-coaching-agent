"""Closed vocabularies for the platform contract.

Every member name may be uppercase; every *serialised value* is lowercase, because the
platform contract requires lowercase enum values on the wire.
"""

from __future__ import annotations

from enum import StrEnum


class LowercaseStrEnum(StrEnum):
    """Base for wire-visible enums. Asserts the lowercase rule at class creation time."""

    def __init_subclass__(cls, **kwargs: object) -> None:  # pragma: no cover - trivial
        super().__init_subclass__(**kwargs)

    def __str__(self) -> str:
        return str(self.value)


class NaricLevel(LowercaseStrEnum):
    """SPECIFIED BY COMPANY. Closed enum. Never an integer scale."""

    LEVEL_3 = "level_3"
    LEVEL_4 = "level_4"
    LEVEL_5 = "level_5"
    LEVEL_6 = "level_6"
    LEVEL_7 = "level_7"
    LEVEL_7_PLUS = "level_7_plus"


#: SPECIFIED BY COMPANY: a value mapping to no enum member is an *invalid response*,
#: not a level. The default is applied and the source marked accordingly.
DEFAULT_NARIC_LEVEL = NaricLevel.LEVEL_5


class NaricLevelSource(LowercaseStrEnum):
    """SPECIFIED BY COMPANY."""

    RETRIEVED = "retrieved"
    DEFAULT = "default"


class ExplanationProfile(LowercaseStrEnum):
    """SPECIFIED BY COMPANY."""

    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class SourceStatus(LowercaseStrEnum):
    """SPECIFIED BY COMPANY.

    ``empty`` and ``unavailable`` are different states and are never conflated:
    ``empty``       -- the upstream answered and had nothing for us,
    ``unavailable`` -- the upstream could not be reached or refused to answer.
    """

    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class RatingValue(LowercaseStrEnum):
    """SPECIFIED BY COMPANY: rating is "up" | "down"."""

    UP = "up"
    DOWN = "down"


class FlagStatus(LowercaseStrEnum):
    """SPECIFIED BY COMPANY: "open" | "reviewed" | "confirmed" | "corrected"."""

    OPEN = "open"
    REVIEWED = "reviewed"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"


class ResponseCategory(LowercaseStrEnum):
    """ASSUMED BY US (names taken from the specification's mock scenario list).

    Rateability never depends on this value -- see :mod:`uc10.application.rating_service`.
    ``UNKNOWN`` exists so that a category this component has never seen is still rateable
    rather than rejected.
    """

    ANSWER = "answer"
    REDIRECT = "redirect"
    REFUSAL = "refusal"
    CLARIFYING_QUESTION = "clarifying_question"
    DEGRADED_FALLBACK = "degraded_fallback"
    UNKNOWN = "unknown"


#: ASSUMED BY US: levels 4 and 6 are not specified by the company; they are grouped with
#: the level below them in the same band. Recorded as assumption A-04.
_PROFILE_BY_LEVEL: dict[NaricLevel, ExplanationProfile] = {
    NaricLevel.LEVEL_3: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_4: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_5: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_6: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_7: ExplanationProfile.ADVANCED,
    NaricLevel.LEVEL_7_PLUS: ExplanationProfile.ADVANCED,
}


def explanation_profile_for(level: NaricLevel) -> ExplanationProfile:
    """Total function over the closed NARIC enum."""
    return _PROFILE_BY_LEVEL[level]


def _assert_lowercase_values() -> None:
    for enum_cls in (
        NaricLevel,
        NaricLevelSource,
        ExplanationProfile,
        SourceStatus,
        RatingValue,
        FlagStatus,
        ResponseCategory,
    ):
        for member in enum_cls:
            assert member.value == member.value.lower(), (enum_cls.__name__, member.name)


_assert_lowercase_values()
