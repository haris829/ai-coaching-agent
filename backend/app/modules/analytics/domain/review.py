"""Content-review action contracts (spec sections 11 and 20).

Review actions are the only write path in UC-10, and every one of them is
auditable: question, decision, administrator and timestamp are all recorded, and
records are never updated or deleted once written.

The administrator identity is taken from the authenticated principal, not from
the request body. A body may echo ``admin_id`` for client-side clarity, but a
value that disagrees with the caller's identity is rejected rather than trusted.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.analytics.domain.analytics import QuestionFlagSummary
from app.modules.analytics.domain.enums import ReviewActionType
from app.modules.analytics.domain.records import ReviewActionRecord

__all__ = [
    "ReviewActionRequest",
    "ReviewActionResponse",
    "ReviewHistoryResponse",
    "ReviewAuditPage",
]


class ReviewActionRequest(BaseModel):
    """Administrator's review decision for one question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str = Field(min_length=1, max_length=255)
    action: ReviewActionType
    note: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional free-text rationale stored with the audit entry.",
    )
    admin_id: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Optional echo of the acting administrator. Must match the authenticated "
            "caller; the authenticated identity is what gets stored."
        ),
    )

    @field_validator("question_id", "admin_id", "note")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ReviewActionResponse(BaseModel):
    """Result of recording a review action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ReviewActionRecord
    flag: QuestionFlagSummary | None = Field(
        default=None,
        description="Flag state after the action. Null when the question had no flag.",
    )
    recorded_at: datetime


class ReviewHistoryResponse(BaseModel):
    """Full audit trail for one question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str
    current_flag: QuestionFlagSummary | None = None
    actions: tuple[ReviewActionRecord, ...] = ()
    total: int = Field(ge=0)
    calculated_at: datetime


class ReviewAuditPage(BaseModel):
    """Paged audit log across questions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ReviewActionRecord, ...] = ()
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    calculated_at: datetime
