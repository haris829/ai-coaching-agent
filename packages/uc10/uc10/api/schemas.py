"""Request and response schemas.

Request models forbid unknown fields outright.  An attempt to send ``rated_at``,
``threshold_applied``, ``down_rate`` or ``user_id`` produces a visible validation error
rather than being quietly ignored: those are server-owned values.

Responses never carry question or response text.  The learner's own comment is returned
to the learner who wrote it and to nobody else.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from uc10.domain.enums import FlagStatus, RatingValue
from uc10.domain.models import MAX_COMMENT_LENGTH, ContentReviewFlag, RatingRecord


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RatingRequest(_StrictRequest):
    """Create or replace a rating.

    ``comment`` is optional in every case. A thumbs down whose comment box was dismissed
    simply omits it -- the rating is still recorded.
    """

    rating: RatingValue
    comment: str | None = Field(default=None, max_length=MAX_COMMENT_LENGTH)


class FlagStatusPatch(_StrictRequest):
    status: FlagStatus


class RatingView(BaseModel):
    """A learner's own rating. Question and response text are deliberately absent."""

    model_config = ConfigDict(frozen=True)

    rating_id: str
    interaction_id: str
    session_id: str
    rating: RatingValue
    comment: str | None
    topic_tag: str
    session_mode: str
    naric_level: str
    rated_at: datetime
    superseded_by: str | None

    @classmethod
    def of(cls, record: RatingRecord) -> RatingView:
        return cls(
            rating_id=record.rating_id,
            interaction_id=record.interaction_id,
            session_id=record.session_id,
            rating=record.rating,
            comment=record.comment,
            topic_tag=record.topic_tag,
            session_mode=record.session_mode,
            naric_level=record.naric_level.value,
            rated_at=record.rated_at,
            superseded_by=record.superseded_by,
        )


class RatingAcceptedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    message: str
    rating: RatingView
    superseded_rating_id: str | None = None


class CurrentRatingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    interaction_id: str
    rating: RatingView | None


class FlagView(BaseModel):
    """A content review flag. Counts, rate, applied rule and identifiers only."""

    model_config = ConfigDict(frozen=True)

    flag_id: str
    topic_tag: str
    window_start: datetime
    window_end: datetime
    total_ratings: int
    down_ratings: int
    down_rate: float
    threshold_applied: float
    minimum_sample_size_applied: int
    flagging_interaction_ids: list[str]
    created_at: datetime
    updated_at: datetime | None
    status: FlagStatus

    @classmethod
    def of(cls, flag: ContentReviewFlag) -> FlagView:
        return cls(
            flag_id=flag.flag_id,
            topic_tag=flag.topic_tag,
            window_start=flag.window_start,
            window_end=flag.window_end,
            total_ratings=flag.total_ratings,
            down_ratings=flag.down_ratings,
            down_rate=flag.down_rate,
            threshold_applied=flag.threshold_applied,
            minimum_sample_size_applied=flag.minimum_sample_size_applied,
            flagging_interaction_ids=list(flag.flagging_interaction_ids),
            created_at=flag.created_at,
            updated_at=flag.updated_at,
            status=flag.status,
        )


class FlagListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    flags: list[FlagView]
    count: int


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: ErrorBody


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    component: str
    wiring: dict[str, str]
