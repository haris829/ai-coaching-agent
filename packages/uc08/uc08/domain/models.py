"""Records UC-08 owns, and the read shapes it expects from upstream ports.

Field-by-field provenance (specified by the company vs assumed by us) is in
``docs/SHARED_CONTRACT.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from uc08.domain.enums import (
    DeliveryStatus,
    ExplanationProfile,
    FreezeOfferStatus,
    NaricLevel,
    NaricLevelSource,
    PersistenceOutcome,
    SessionIdSource,
    SourceStatus,
    StreakOutcome,
)
from uc08.domain.time_utils import ensure_utc

CompletionPercent = Annotated[int, Field(ge=0, le=100)]


class _Record(BaseModel):
    """Frozen, closed-shape base. Unknown fields are an error, not a shrug."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _UtcRecord(_Record):
    @field_validator("*", mode="before")
    @classmethod
    def _reject_naive_datetimes(cls, value: object) -> object:
        if isinstance(value, datetime):
            return ensure_utc(value)
        return value


# --------------------------------------------------------------------------
# Upstream read shapes (ActivityProvider / GapReportProvider)
# --------------------------------------------------------------------------
class ActivityInteraction(_UtcRecord):
    """One coaching interaction as reported by the activity read model."""

    interaction_id: str
    occurred_at: datetime


class Topic(_UtcRecord):
    """A topic, as surfaced by the gap report port.

    ``naric_level`` is always a platform enum member: an upstream value mapping
    to no member yields ``LEVEL_5`` / ``source=default`` / ``status=invalid``.
    """

    topic_id: str
    name: str
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    naric_level_status: SourceStatus
    explanation_profile: ExplanationProfile
    course_progress_percent: CompletionPercent | None = None
    course_progress_status: SourceStatus = SourceStatus.EMPTY


class ActivityWindowRead(_UtcRecord):
    """Interactions in a window, plus the status of that read."""

    interactions: tuple[ActivityInteraction, ...] = ()
    status: SourceStatus = SourceStatus.AVAILABLE


class TopicMention(_UtcRecord):
    """A topic touched in the requested window.

    ``first_mentioned_at`` is the earliest mention at or after the ``since``
    boundary of the read. It lets a caller narrow a window that the port
    signature can only open (the port takes ``since`` and no ``until``), which
    is what the weekly summary needs to say "last week" and mean it (A-16).
    """

    name: str
    first_mentioned_at: datetime


class TopicsRead(_UtcRecord):
    topics: tuple[TopicMention, ...] = ()
    status: SourceStatus = SourceStatus.AVAILABLE


class QuestionCountRead(_UtcRecord):
    count: int = Field(ge=0)
    status: SourceStatus = SourceStatus.AVAILABLE


# --------------------------------------------------------------------------
# Streak record -- owned by this component. Shape fixed by the platform.
# --------------------------------------------------------------------------
class StreakRecord(_UtcRecord):
    user_id: str
    current_streak_days: int = Field(ge=0)
    longest_streak_days: int = Field(ge=0)
    last_activity_at: datetime | None
    streak_started_at: datetime | None
    freeze_available: bool
    freeze_used_at: datetime | None
    updated_at: datetime

    @field_validator("longest_streak_days")
    @classmethod
    def _longest_is_a_high_water_mark(cls, value: int, info) -> int:
        current = info.data.get("current_streak_days")
        if current is not None and value < current:
            raise ValueError("longest_streak_days must never be below current_streak_days")
        return value


# --------------------------------------------------------------------------
# Badge record -- owned by this component. Shape fixed by the platform.
# --------------------------------------------------------------------------
class Badge(_UtcRecord):
    badge_id: str
    user_id: str
    milestone: int = Field(ge=1)
    awarded_at: datetime
    question_count_at_award: int = Field(ge=0)


# --------------------------------------------------------------------------
# Freeze offer -- owned by this component (A-10, A-11, A-12)
# --------------------------------------------------------------------------
class FreezeOffer(_UtcRecord):
    offer_id: str
    user_id: str
    status: FreezeOfferStatus
    offered_at: datetime
    expires_at: datetime
    #: The streak the learner held before the reset this offer can undo.
    preserved_streak_days: int = Field(ge=1)
    preserved_streak_started_at: datetime | None
    answered_at: datetime | None = None

    def is_open_at(self, moment: datetime) -> bool:
        return self.status is FreezeOfferStatus.OFFERED and ensure_utc(moment) < self.expires_at


# --------------------------------------------------------------------------
# Weekly summary -- owned by this component
# --------------------------------------------------------------------------
class WeeklySummary(_UtcRecord):
    summary_id: str
    user_id: str
    #: ISO week key of the week being summarised, e.g. ``2026-W34``.
    week: str
    week_start_at: datetime
    week_end_at: datetime
    generated_at: datetime

    topics_covered: tuple[str, ...]
    topics_status: SourceStatus
    questions_asked: int = Field(ge=0)
    questions_asked_status: SourceStatus
    current_streak_days: int = Field(ge=0)

    suggested_topic: Topic | None
    suggested_topic_status: SourceStatus
    #: Names of elements deliberately left out, with a human-readable reason.
    omissions: tuple[str, ...] = ()
    omission_notes: tuple[str, ...] = ()

    delivery_status: DeliveryStatus = DeliveryStatus.PENDING
    send_attempts: int = Field(default=0, ge=0)
    last_send_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    next_retry_at: datetime | None = None
    #: ISO week keys that were never generated, because UC-08 does not
    #: batch-send missed weeks.
    skipped_weeks: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Events and incidents
# --------------------------------------------------------------------------
class BadgeAwardedEvent(_UtcRecord):
    """In-chat notification payload for a caller to render.

    UC-08 renders nothing; it emits this and stops.
    """

    event_id: str
    event_type: str = "badge_awarded"
    user_id: str
    badge_id: str
    milestone: int
    question_count_at_award: int
    awarded_at: datetime
    occurred_at: datetime


class WeeklySummaryEvent(_UtcRecord):
    event_id: str
    event_type: str = "weekly_summary"
    user_id: str
    summary_id: str
    week: str
    occurred_at: datetime
    summary: WeeklySummary


class StreakWriteIncident(_UtcRecord):
    """Handed to ``EngineeringAlertSink`` when a streak write cannot commit."""

    incident_id: str
    user_id: str
    occurred_at: datetime
    attempts: int = Field(ge=1)
    #: The count that remains authoritative. Never a reset value produced by
    #: this failure.
    preserved_streak_days: int = Field(ge=0)
    preserved_longest_streak_days: int = Field(ge=0)
    intended_streak_days: int = Field(ge=0)
    error_type: str
    error_detail: str


# --------------------------------------------------------------------------
# Application results
# --------------------------------------------------------------------------
class RecordActivityResult(_UtcRecord):
    streak: StreakRecord
    outcome: StreakOutcome
    persistence_outcome: PersistenceOutcome
    idempotent_replay: bool
    session_id: str
    session_id_source: SessionIdSource
    awarded_badges: tuple[Badge, ...] = ()
    badge_events: tuple[BadgeAwardedEvent, ...] = ()
    freeze_offer: FreezeOffer | None = None
    question_count: int | None = None
    question_count_status: SourceStatus = SourceStatus.AVAILABLE
    activity_status: SourceStatus = SourceStatus.AVAILABLE


class WeeklySummaryRunResult(_UtcRecord):
    generated: WeeklySummary | None
    #: True when the summary for the target week already existed.
    already_generated: bool = False
    retried: WeeklySummary | None = None
    reason: str = ""
    skipped_weeks: tuple[str, ...] = ()
