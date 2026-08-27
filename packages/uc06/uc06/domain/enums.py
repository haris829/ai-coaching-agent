"""Closed vocabularies fixed by the platform contract. Do not rename members."""

from __future__ import annotations

from enum import Enum


class NaricLevel(str, Enum):
    """Closed enum. A value mapping to no member is an invalid provider response,
    never a level. Adapters must raise ProviderInvalidResponse, never guess."""

    LEVEL_3 = "LEVEL_3"
    LEVEL_4 = "LEVEL_4"
    LEVEL_5 = "LEVEL_5"
    LEVEL_6 = "LEVEL_6"
    LEVEL_7 = "LEVEL_7"
    LEVEL_7_PLUS = "LEVEL_7_PLUS"


#: Platform default when learner context cannot be retrieved. A context failure
#: never removes a safety control and never blocks an answer.
DEFAULT_NARIC_LEVEL = NaricLevel.LEVEL_5


class NaricLevelSource(str, Enum):
    RETRIEVED = "retrieved"
    DEFAULT = "default"


class ExplanationProfile(str, Enum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


#: Levels 4 and 6 are an assumption (docs/assumptions.md row A-07): the scope
#: document names 3/5/7 explicitly and is silent on where 4 and 6 fall.
PROFILE_BY_LEVEL: dict[NaricLevel, ExplanationProfile] = {
    NaricLevel.LEVEL_3: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_4: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_5: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_6: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_7: ExplanationProfile.ADVANCED,
    NaricLevel.LEVEL_7_PLUS: ExplanationProfile.ADVANCED,
}


def profile_for(level: NaricLevel) -> ExplanationProfile:
    return PROFILE_BY_LEVEL[level]


class SourceStatus(str, Enum):
    """`empty` and `unavailable` are different states and are never conflated.

    available   - source responded and carried usable content
    empty       - source responded successfully and legitimately held nothing
    partial     - source responded, some expected sections missing
    unavailable - source could not be reached or refused; we hold no knowledge
    invalid     - source responded with a shape or value we cannot map
    """

    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class GuardClass(str, Enum):
    NONE = "none"
    OUTCOME_PREDICTION = "outcome_prediction"
    LITIGATION_STRATEGY = "litigation_strategy"


class RatingState(str, Enum):
    PENDING = "pending"
    RATED = "rated"


class ResponseMode(str, Enum):
    """UC-06 emits case_linked responses. general_fallback is the non-case-linked
    degraded mode used when the case file cannot be read; it carries no facts."""

    CASE_LINKED = "case_linked"
    GENERAL_FALLBACK = "general_fallback"


class SecurityIncidentKind(str, Enum):
    PROMPT_DISCLAIMER_SUPPRESSION = "prompt_disclaimer_suppression"
    REQUEST_FIELD_SUPPRESSION = "request_field_suppression"
    INTERNAL_DISCLAIMER_ABSENT = "internal_disclaimer_absent"
    INTERNAL_DISCLAIMER_ALTERED = "internal_disclaimer_altered"
    UNAUTHORISED_CASE_ACCESS = "unauthorised_case_access"
