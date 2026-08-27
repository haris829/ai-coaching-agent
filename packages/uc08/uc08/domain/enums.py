"""Closed vocabularies for UC-08.

Every serialised enum value is lowercase. Python member names may be uppercase;
the value that crosses the boundary is not.
"""

from __future__ import annotations

from enum import Enum


class NaricLevel(str, Enum):
    """Platform NARIC level. Closed set, fixed by the platform contract.

    Never an integer scale. Never a three-point pedagogic scale.
    """

    LEVEL_3 = "level_3"
    LEVEL_4 = "level_4"
    LEVEL_5 = "level_5"
    LEVEL_6 = "level_6"
    LEVEL_7 = "level_7"
    LEVEL_7_PLUS = "level_7_plus"


#: Applied when an upstream supplies no level, or a value mapping to no member.
DEFAULT_NARIC_LEVEL = NaricLevel.LEVEL_5


class NaricLevelSource(str, Enum):
    RETRIEVED = "retrieved"
    DEFAULT = "default"


class ExplanationProfile(str, Enum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class SourceStatus(str, Enum):
    """Status of a single upstream read.

    ``EMPTY`` (the source answered, and the answer is "nothing") and
    ``UNAVAILABLE`` (the source did not answer) are different states and are
    never conflated.
    """

    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class StreakOutcome(str, Enum):
    """What a single ``record_activity`` call did to the streak count."""

    STARTED = "started"
    #: Activity on a day already counted. Count unchanged (once-per-day rule).
    UNCHANGED_SAME_DAY = "unchanged_same_day"
    INCREMENTED = "incremented"
    #: Genuine inactivity determination. Count reset to 1.
    RESET = "reset"
    #: The activity read model could not answer and the persisted record showed
    #: no qualifying activity either. The count is preserved, not reset: a
    #: source outage is a system problem, and the learner does not pay for it.
    UNCHANGED_SOURCE_DEGRADED = "unchanged_source_degraded"
    #: Replay of an already-processed interaction. Nothing changed.
    IDEMPOTENT_REPLAY = "idempotent_replay"


class FreezeOfferStatus(str, Enum):
    OFFERED = "offered"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class PersistenceOutcome(str, Enum):
    """Result of persisting a streak record."""

    SAVED = "saved"
    SAVED_ON_RETRY = "saved_on_retry"
    #: Both attempts failed. The last known count was preserved and engineering
    #: was alerted. Never a reset.
    PRESERVED_LAST_KNOWN = "preserved_last_known"


class SessionIdSource(str, Enum):
    RECEIVED = "received"
    DEV_MINTED = "dev_minted"


EXPLANATION_PROFILE_BY_LEVEL: dict[NaricLevel, ExplanationProfile] = {
    NaricLevel.LEVEL_3: ExplanationProfile.BASIC,
    # A-06: levels 4 and 6 are assumed, not specified by the company.
    NaricLevel.LEVEL_4: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_5: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_6: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_7: ExplanationProfile.ADVANCED,
    NaricLevel.LEVEL_7_PLUS: ExplanationProfile.ADVANCED,
}
