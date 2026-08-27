"""Request and response schemas.

Two rules hold across every schema here:

1. **No request accepts a user identifier.** The account is resolved
   server-side by the identity port. There is no path segment, query parameter
   or body field a learner could change to reach another learner data.
2. **Unknown fields are rejected outright.** ``extra="forbid"`` means an attempt
   to send ``current_streak_days``, ``milestone`` or ``freeze_available``
   produces a visible 422, not a silent ignore.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from uc08.domain.enums import (
    DeliveryStatus,
    FreezeOfferStatus,
    PersistenceOutcome,
    SessionIdSource,
    SourceStatus,
    StreakOutcome,
)
from uc08.domain.models import Badge, BadgeAwardedEvent, StreakRecord, Topic, WeeklySummary


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------
class RecordActivityRequest(_Schema):
    """Called when a coaching interaction occurs.

    ``interaction_id`` is the idempotency key: replaying it changes nothing.
    ``session_id`` is opaque and is received, never created -- unless dev
    minting is explicitly enabled.
    """

    interaction_id: str = Field(min_length=1, max_length=200)
    session_id: str | None = Field(default=None, max_length=200)


class AcceptFreezeRequest(_Schema):
    """Accept the offered freeze. No fields: the account is server-side and the
    offer is the one currently open for it."""


class GenerateWeeklySummaryRequest(_Schema):
    """Trigger a generation run for the authenticated account. No fields."""


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------
class FreezeOfferResponse(_Schema):
    offer_id: str
    status: FreezeOfferStatus
    offered_at: datetime
    expires_at: datetime
    preserved_streak_days: int
    answered_at: datetime | None


class StreakResponse(_Schema):
    """The streak record, exactly as this component owns it."""

    current_streak_days: int
    longest_streak_days: int
    last_activity_at: datetime | None
    streak_started_at: datetime | None
    freeze_available: bool
    freeze_used_at: datetime | None
    updated_at: datetime


class StreakStateResponse(_Schema):
    streak: StreakResponse
    freeze_offer: FreezeOfferResponse | None
    badges: tuple[Badge, ...]
    window_hours: int
    freeze_min_streak_days: int


class RecordActivityResponse(_Schema):
    streak: StreakResponse
    outcome: StreakOutcome
    persistence_outcome: PersistenceOutcome
    idempotent_replay: bool
    session_id: str
    session_id_source: SessionIdSource
    activity_status: SourceStatus
    question_count: int | None
    question_count_status: SourceStatus
    awarded_badges: tuple[Badge, ...]
    #: In-chat notification events for the caller to render. UC-08 renders none.
    badge_events: tuple[BadgeAwardedEvent, ...]
    freeze_offer: FreezeOfferResponse | None


class BadgeCollectionResponse(_Schema):
    badges: tuple[Badge, ...]
    milestones: tuple[int, ...]


class WeeklySummaryResponse(_Schema):
    summary_id: str
    week: str
    week_start_at: datetime
    week_end_at: datetime
    generated_at: datetime
    topics_covered: tuple[str, ...]
    topics_status: SourceStatus
    questions_asked: int
    questions_asked_status: SourceStatus
    current_streak_days: int
    suggested_topic: Topic | None
    suggested_topic_status: SourceStatus
    omissions: tuple[str, ...]
    omission_notes: tuple[str, ...]
    delivery_status: DeliveryStatus
    send_attempts: int
    last_send_attempt_at: datetime | None
    sent_at: datetime | None
    next_retry_at: datetime | None
    skipped_weeks: tuple[str, ...]


class WeeklySummaryCollectionResponse(_Schema):
    summaries: tuple[WeeklySummaryResponse, ...]


class GenerateWeeklySummaryResponse(_Schema):
    generated: WeeklySummaryResponse | None
    already_generated: bool
    retried: WeeklySummaryResponse | None
    reason: str
    skipped_weeks: tuple[str, ...]


class HealthResponse(_Schema):
    status: str
    component: str
    now: datetime
    activity_provider: str
    gap_report_provider: str
    persistence: str


class ErrorResponse(_Schema):
    error: str
    detail: str


# --------------------------------------------------------------------------
# Mapping helpers
# --------------------------------------------------------------------------
def streak_response(record: StreakRecord) -> StreakResponse:
    return StreakResponse(
        current_streak_days=record.current_streak_days,
        longest_streak_days=record.longest_streak_days,
        last_activity_at=record.last_activity_at,
        streak_started_at=record.streak_started_at,
        freeze_available=record.freeze_available,
        freeze_used_at=record.freeze_used_at,
        updated_at=record.updated_at,
    )


def freeze_offer_response(offer) -> FreezeOfferResponse | None:
    if offer is None:
        return None
    return FreezeOfferResponse(
        offer_id=offer.offer_id,
        status=offer.status,
        offered_at=offer.offered_at,
        expires_at=offer.expires_at,
        preserved_streak_days=offer.preserved_streak_days,
        answered_at=offer.answered_at,
    )


def weekly_summary_response(summary: WeeklySummary) -> WeeklySummaryResponse:
    return WeeklySummaryResponse(
        summary_id=summary.summary_id,
        week=summary.week,
        week_start_at=summary.week_start_at,
        week_end_at=summary.week_end_at,
        generated_at=summary.generated_at,
        topics_covered=summary.topics_covered,
        topics_status=summary.topics_status,
        questions_asked=summary.questions_asked,
        questions_asked_status=summary.questions_asked_status,
        current_streak_days=summary.current_streak_days,
        suggested_topic=summary.suggested_topic,
        suggested_topic_status=summary.suggested_topic_status,
        omissions=summary.omissions,
        omission_notes=summary.omission_notes,
        delivery_status=summary.delivery_status,
        send_attempts=summary.send_attempts,
        last_send_attempt_at=summary.last_send_attempt_at,
        sent_at=summary.sent_at,
        next_retry_at=summary.next_retry_at,
        skipped_weeks=summary.skipped_weeks,
    )
