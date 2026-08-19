"""Question request/response contracts.

Deliberately permissive on input: pydantic only checks that the request is *well-formed*
(right JSON shape, sane lengths). Whether it describes a *valid question* is decided by
``domain/validator.py``, which is the authoritative layer and produces far better field-level
messages. This keeps one set of rules rather than two that can drift apart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.core.schemas import CamelModel
from app.modules.question_bank.domain.enums import (
    Difficulty,
    QuestionStatus,
    QuestionType,
    ScoringStrategy,
)
from app.modules.question_bank.schemas.topic import TopicRef

# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class OptionIn(CamelModel):
    label: str | None = None
    text: str | None = None
    #: Default presentation order. Assigned from array order when omitted.
    position: int | None = None
    is_correct: bool = False
    #: SCENARIO only: marks the primary answer.
    is_primary: bool = False
    #: DRAG_TO_ORDER only: 1-based rank in the correct answer order.
    correct_position: int | None = None
    feedback: str | None = None


class ScoringIn(CamelModel):
    points: float | None = None
    #: Free string, not the enum, so an unrecognised strategy is reported by the domain
    #: validator (which names the valid values and the types each is allowed for) rather than
    #: by a generic pydantic enum error.
    scoring_strategy: str | None = None
    penalty_per_incorrect: float | None = None


class QuestionCreate(CamelModel):
    #: Accepted as a free string so an unknown type yields a domain error naming the valid
    #: options, rather than a generic pydantic enum error.
    type: str
    question_text: str | None = None
    scenario_text: str | None = None
    explanation: str | None = None
    difficulty: str | None = None
    status: str | None = None
    external_ref: str | None = Field(default=None, max_length=128)
    options: list[OptionIn] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    topic_ids: list[str] = Field(default_factory=list)
    scoring: ScoringIn = Field(default_factory=ScoringIn)


class QuestionUpdate(CamelModel):
    """Partial update.

    Any field left unset keeps its current value; the merged result is then re-validated in
    full, so a question can never be edited into an invalid state.
    """

    type: str | None = None
    question_text: str | None = None
    scenario_text: str | None = None
    explanation: str | None = None
    difficulty: str | None = None
    external_ref: str | None = Field(default=None, max_length=128)
    options: list[OptionIn] | None = None
    topics: list[str] | None = None
    topic_ids: list[str] | None = None
    scoring: ScoringIn | None = None
    #: Allowed transitions: DRAFT <-> ACTIVE. Use /retire and /reactivate for RETIRED.
    status: str | None = None


class RetireRequest(CamelModel):
    reason: str | None = Field(default=None, max_length=2_000)


class ReactivateRequest(CamelModel):
    reason: str | None = Field(default=None, max_length=2_000)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class OptionOut(CamelModel):
    id: str
    label: str
    text: str
    #: Default presentation order — NOT the answer.
    position: int
    is_correct: bool
    is_primary: bool
    #: Correct answer order (DRAG_TO_ORDER only).
    correct_position: int | None
    feedback: str | None


class ScoringOut(CamelModel):
    points: float
    scoring_strategy: ScoringStrategy
    penalty_per_incorrect: float


class UsageSummary(CamelModel):
    """How the question has been used. Drives the UI's retire-vs-delete decision."""

    total: int
    completed: int
    in_progress: int
    #: A question with completed usage may never be hard-deleted (UC-02 §6, §15).
    has_history: bool
    can_hard_delete: bool


class QuestionOut(CamelModel):
    id: str
    reference: str
    seq: int
    external_ref: str | None
    type: QuestionType
    status: QuestionStatus
    question_text: str
    scenario_text: str | None
    explanation: str | None
    difficulty: Difficulty | None
    scoring: ScoringOut
    version: int
    content_hash: str
    options: list[OptionOut]
    topics: list[TopicRef]
    #: Correct answer labels (choice types).
    correct_labels: list[str]
    #: Correct label sequence (DRAG_TO_ORDER).
    correct_order: list[str]
    primary_label: str | None
    retired_at: datetime | None
    retired_reason: str | None
    retired_by: str | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None
    import_id: str | None
    import_row_number: int | None
    #: True when the question is eligible for future quiz delivery.
    is_deliverable: bool
    usage: UsageSummary | None = None


class QuestionListItem(CamelModel):
    """Trimmed row for the admin list screen (UC-02 §6)."""

    id: str
    reference: str
    type: QuestionType
    status: QuestionStatus
    question_text: str
    topics: list[TopicRef]
    points: float
    scoring_strategy: ScoringStrategy
    difficulty: Difficulty | None
    version: int
    option_count: int
    usage_count: int
    is_deliverable: bool
    created_at: datetime
    updated_at: datetime


class SnapshotOut(CamelModel):
    id: str
    question_id: str
    version: int
    reference: str
    type: QuestionType
    status: str
    question_text: str
    scenario_text: str | None
    explanation: str | None
    points: float
    scoring_strategy: str
    penalty_per_incorrect: float
    content_hash: str
    payload: dict[str, Any]
    created_at: datetime


class DeleteResult(CamelModel):
    id: str
    reference: str
    deleted: bool
    message: str
