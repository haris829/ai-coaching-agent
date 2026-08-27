"""Records this component owns or expects to receive.

Field-level provenance:
  * SPECIFIED BY COMPANY -- named in the platform contract we were handed.
  * ASSUMED BY US        -- invented here; every one has a row in docs/assumptions.md.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from uc10.domain.enums import (
    ExplanationProfile,
    FlagStatus,
    NaricLevel,
    NaricLevelSource,
    RatingValue,
    ResponseCategory,
    SourceStatus,
)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,127}$")

#: ASSUMED BY US (A-06): the maximum length of a learner's one-line comment.
MAX_COMMENT_LENGTH = 500


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _as_slug(value: str, field: str) -> str:
    slug = value.strip().lower().replace(" ", "_")
    if not _SLUG.match(slug):
        raise ValueError(f"{field} must be a lowercase slug")
    return slug


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


class InteractionRecord(_Record):
    """The shape this component EXPECTS TO RECEIVE from an InteractionProvider.

    Read-only to this component: nothing here is ever written back upstream.
    """

    interaction_id: str = Field(min_length=1)          # SPECIFIED
    session_id: str = Field(min_length=1)              # SPECIFIED (opaque; never minted here)
    user_id: str = Field(min_length=1)                 # SPECIFIED
    question_text: str                                 # SPECIFIED
    response_text: str                                 # SPECIFIED
    response_category: ResponseCategory                # ASSUMED (A-05)
    topic_tag: str                                     # SPECIFIED (vocabulary owned upstream)
    session_mode: str                                  # SPECIFIED (vocabulary owned upstream)
    naric_level: NaricLevel                            # SPECIFIED
    naric_level_source: NaricLevelSource               # SPECIFIED
    explanation_profile: ExplanationProfile            # SPECIFIED (derived from level)
    naric_source_status: SourceStatus                  # ASSUMED (A-07)
    course_completion_percent: int | None = Field(default=None, ge=0, le=100)  # SPECIFIED
    delivered_at: datetime                             # SPECIFIED (server-side truth)
    source_status: SourceStatus                        # SPECIFIED

    @field_validator("delivered_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _as_utc(v)

    @field_validator("topic_tag", "session_mode")
    @classmethod
    def _slug(cls, v: str, info) -> str:
        return _as_slug(v, info.field_name)


class RatingRecord(_Record):
    """OWNED BY THIS COMPONENT. Field set is exactly the specified metadata set."""

    rating_id: str
    interaction_id: str
    session_id: str
    user_id: str
    rating: RatingValue
    comment: str | None = Field(default=None, max_length=MAX_COMMENT_LENGTH)
    question_text: str
    response_text: str
    naric_level: NaricLevel
    session_mode: str
    topic_tag: str
    rated_at: datetime
    superseded_by: str | None = None

    @field_validator("rated_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _as_utc(v)

    @property
    def is_current(self) -> bool:
        return self.superseded_by is None

    def superseded(self, by_rating_id: str) -> RatingRecord:
        """Return the same record marked superseded. The original is retained, not deleted."""
        return self.model_copy(update={"superseded_by": by_rating_id})


class ContentReviewFlag(_Record):
    """OWNED BY THIS COMPONENT. Carries counts and identifiers -- never learner content."""

    flag_id: str
    topic_tag: str
    window_start: datetime
    window_end: datetime
    total_ratings: int = Field(ge=0)
    down_ratings: int = Field(ge=0)
    down_rate: float = Field(ge=0.0, le=1.0)
    threshold_applied: float = Field(ge=0.0, le=1.0)
    flagging_interaction_ids: tuple[str, ...]
    created_at: datetime
    status: FlagStatus = FlagStatus.OPEN
    # ASSUMED BY US (A-08): the sample-size half of the rule that produced this flag.
    minimum_sample_size_applied: int = Field(ge=1)
    # ASSUMED BY US (A-09): set when an existing open flag is updated rather than re-raised.
    updated_at: datetime | None = None

    @field_validator("window_start", "window_end", "created_at", "updated_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _as_utc(v)

    @property
    def is_open(self) -> bool:
        return self.status is FlagStatus.OPEN


#: The field names the specification requires on a rating record, used by the schema test.
REQUIRED_RATING_FIELDS: frozenset[str] = frozenset(
    {
        "rating_id",
        "interaction_id",
        "session_id",
        "user_id",
        "rating",
        "comment",
        "question_text",
        "response_text",
        "naric_level",
        "session_mode",
        "topic_tag",
        "rated_at",
        "superseded_by",
    }
)

#: The field names the specification requires on a content review flag.
REQUIRED_FLAG_FIELDS: frozenset[str] = frozenset(
    {
        "flag_id",
        "topic_tag",
        "window_start",
        "window_end",
        "total_ratings",
        "down_ratings",
        "down_rate",
        "threshold_applied",
        "flagging_interaction_ids",
        "created_at",
        "status",
    }
)

#: Fields on the flag that this component added and the company has not specified.
ASSUMED_FLAG_FIELDS: frozenset[str] = frozenset({"minimum_sample_size_applied", "updated_at"})

#: Fields that must never be carried on a flag or written to a log.
LEARNER_CONTENT_FIELDS: frozenset[str] = frozenset({"question_text", "response_text", "comment"})
