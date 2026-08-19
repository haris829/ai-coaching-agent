"""Delivery + historical reporting contracts.

INTEGRATION SEAM. These endpoints are the contract the quiz-delivery / attempt module (built
outside UC-02) consumes:

* ``DeliverableQuestion`` — the sanitised question to present. Note it carries no answer key.
* ``RecordUsageRequest`` — "I delivered this question to this attempt", pinning the snapshot.
* ``RecordResponseRequest`` — "here is the learner's response / the attempt completed".
* ``AttemptReport`` — historical reporting, rendered from snapshots so it survives retirement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.core.schemas import CamelModel
from app.modules.question_bank.domain.enums import AttemptStatus, QuestionType
from app.modules.question_bank.schemas.topic import TopicRef


class DeliverableOption(CamelModel):
    label: str
    text: str
    #: Default presentation order. Delivery is free to shuffle; see `presentationOrder` on
    #: the usage record for what was actually shown.
    position: int


class DeliverableQuestion(CamelModel):
    """A question eligible for future delivery, with the answer key withheld."""

    id: str
    reference: str
    version: int
    type: QuestionType
    question_text: str
    scenario_text: str | None
    difficulty: str | None
    points: float
    scoring_strategy: str
    options: list[DeliverableOption]
    topics: list[TopicRef]


class DeliveryPoolResponse(CamelModel):
    #: Only ACTIVE questions ever appear here. Retired questions are excluded by construction.
    items: list[DeliverableQuestion]
    total_available: int
    requested: int


class RecordUsageRequest(CamelModel):
    #: Opaque attempt identifier owned by the delivery module (not a FK — see models.py).
    attempt_ref: str = Field(min_length=1, max_length=128)
    question_id: str = Field(min_length=1)
    learner_ref: str | None = Field(default=None, max_length=128)
    #: The order the options were actually shown in. Recorded separately from the answer key.
    presentation_order: list[str] | None = None


class RecordResponseRequest(CamelModel):
    #: Choice types (SINGLE_CHOICE / TRUE_FALSE / MULTI_SELECT / SCENARIO).
    selected_labels: list[str] | None = None
    #: DRAG_TO_ORDER: the learner's ordering, as option labels.
    ordered_labels: list[str] | None = None
    attempt_status: AttemptStatus = AttemptStatus.COMPLETED


class UsageOut(CamelModel):
    id: str
    attempt_ref: str
    learner_ref: str | None
    question_id: str
    #: Preserved question identity — still resolvable after retirement.
    question_reference: str
    snapshot_id: str
    snapshot_version: int
    #: 1-based position this question occupied in the attempt, when the caller reported one. Stored
    #: since the delivery-position migration but previously not exposed, which made it impossible
    #: for a client to render an attempt's questions in the order the learner actually saw them.
    delivery_position: int | None
    attempt_status: AttemptStatus
    learner_response: dict[str, Any] | None
    presentation_order: list[str] | None
    is_correct: bool | None
    awarded_points: float | None
    max_points: float | None
    delivered_at: datetime
    responded_at: datetime | None
    completed_at: datetime | None


class AttemptReportItem(CamelModel):
    """One question as it appeared in a completed attempt.

    Every field here is read from the frozen snapshot, so it is identical before and after the
    question is edited or retired (UC-02 §16).
    """

    question_id: str
    question_reference: str
    snapshot_version: int
    #: The question's status in the LIVE bank right now (e.g. RETIRED) — reporting context.
    current_question_status: str
    type: QuestionType
    question_text: str
    scenario_text: str | None
    explanation: str | None
    options: list[dict[str, Any]]
    correct_labels: list[str]
    correct_order: list[str]
    topics: list[str]
    learner_response: dict[str, Any] | None
    presentation_order: list[str] | None
    is_correct: bool | None
    awarded_points: float | None
    max_points: float | None
    delivered_at: datetime
    completed_at: datetime | None


class AttemptReport(CamelModel):
    attempt_ref: str
    learner_ref: str | None
    attempt_status: str
    question_count: int
    total_awarded_points: float
    total_max_points: float
    items: list[AttemptReportItem]
