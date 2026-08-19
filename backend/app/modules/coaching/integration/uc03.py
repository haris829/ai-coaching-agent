"""UC-03 (Quiz Attempt Delivery) — the contract UC-07 consumes (§2, §7, §8).

UC-07 reads three things from UC-03 and writes nothing:

1. **the attempt** — who attempted what, and whether it has been submitted;
2. **the delivered questions** — the paper *as the learner actually saw it*, which is what a
   coaching conversation must be about: the same prompt, the same options, the same order;
3. **the learner answers** — exactly what was submitted.

Two rules live in this file.

**Coaching is post-submission only (§7, §8).** ``ELIGIBLE_ATTEMPT_STATUSES`` contains one member.
An ACTIVE or SUBMISSION_PENDING attempt is a quiz in progress, and coaching a learner through a
question they can still change the answer to is simply cheating with extra steps. The check is
made in the domain, not left to a frontend that hides a button (§8).

**The delivered snapshot, not today's question bank.** Options may have been shuffled or the
question edited since; coaching about a question the learner never saw would be worse than no
coaching.

WHAT THIS PORT DELIBERATELY DOES NOT CARRY
------------------------------------------
``DeliveredOption`` has no ``is_correct`` flag. UC-06's equivalent port has one, because a feedback
report legitimately shows the learner the right answer. Coaching does not, so the field is absent
from the type — the first of several layers that make answer-key leakage a compile-time-shaped
problem rather than a review-time one (§12, §26).

``metadata`` is the honest exception. Real delivery records carry provider-specific blobs, and
pretending they do not would mean the sanitiser had nothing to defend against. It is accepted here,
labelled untrusted, and dropped wholesale at the sanitisation boundary (§13).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.core.question_types import QuestionType


class AttemptStatus(StrEnum):
    """The attempt lifecycle values UC-07 cares about. UC-03 owns the full lifecycle.

    ``NOT_STARTED``/``ABANDONED`` are included so the coaching gate can name the actual state it
    refused, rather than lumping everything into "not submitted" (§7).
    """

    NOT_STARTED = "NOT_STARTED"
    ACTIVE = "ACTIVE"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    SUBMITTED = "SUBMITTED"
    ABANDONED = "ABANDONED"


#: The single attempt state in which coaching may be offered (§7, §8).
ELIGIBLE_ATTEMPT_STATUSES: frozenset[AttemptStatus] = frozenset({AttemptStatus.SUBMITTED})

#: Re-exported from the shared kernel so UC-07's own modules have one import to reach for, exactly
#: as UC-03 and UC-04 do. UC-07 does not *define* question types — there is one vocabulary for the
#: whole system (``app.core.question_types``) — and it must not refuse an unknown one: a type it has
#: no special rendering for is still coachable from its prompt and its topic.
__all__ = [
    "ELIGIBLE_ATTEMPT_STATUSES",
    "AttemptContext",
    "AttemptProvider",
    "AttemptStatus",
    "DeliveredOption",
    "DeliveredOrderItem",
    "DeliveredQuestion",
    "LearnerAnswer",
    "QuestionType",
]


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """A submitted attempt, as recorded by UC-03."""

    attempt_id: str
    learner_id: str
    course_id: str
    course_name: str
    quiz_id: str
    status: AttemptStatus
    attempt_number: int = 1
    started_at: str | None = None
    submitted_at: str | None = None

    @property
    def submitted(self) -> bool:
        return self.status in ELIGIBLE_ATTEMPT_STATUSES


@dataclass(frozen=True, slots=True)
class DeliveredOption:
    """A selectable option exactly as presented to the learner.

    No correctness flag exists on this type, by design. The full option set *as delivered* is safe
    to show the coach precisely because it discriminates nothing: the model sees the same four
    choices the learner saw, in the same order, with no marker saying which one was right (§12).
    """

    option_id: str
    text: str | None = None
    #: Position as shown. Preserved verbatim — re-sorting the options could itself encode the key.
    position: int | None = None


@dataclass(frozen=True, slots=True)
class DeliveredOrderItem:
    """An orderable item for DRAG_TO_ORDER questions, as presented (shuffled, not solved)."""

    item_id: str
    text: str | None = None
    position: int | None = None


@dataclass(frozen=True, slots=True)
class DeliveredQuestion:
    """One question as delivered.

    ``metadata`` is an untrusted passthrough: whatever UC-03's record happened to carry. It is
    never read by the domain and never survives sanitisation (§13).
    """

    question_id: str
    #: 1-based position within the delivered paper, so a review queue reads in the learner's order.
    position: int
    question_type: QuestionType
    prompt: str | None = None
    scenario_text: str | None = None
    options: tuple[DeliveredOption, ...] = field(default_factory=tuple)
    order_items: tuple[DeliveredOrderItem, ...] = field(default_factory=tuple)
    topics: tuple[str, ...] = field(default_factory=tuple)
    maximum_marks: float | None = None
    question_reference: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def option(self, option_id: str) -> DeliveredOption | None:
        return next((item for item in self.options if item.option_id == option_id), None)

    def order_item(self, item_id: str) -> DeliveredOrderItem | None:
        return next((item for item in self.order_items if item.item_id == item_id), None)


@dataclass(frozen=True, slots=True)
class LearnerAnswer:
    """A learner's frozen answer to one delivered question.

    ``response`` is UC-03's canonical payload, kept as a mapping rather than a parsed union on
    purpose: UC-07 describes it back to the learner and must not become a second validator of
    another module's answer format. The canonical shapes are UC-03/UC-04's::

        {"type": "SINGLE_CHOICE", "selected_option_id": "A"}
        {"type": "TRUE_FALSE",    "value": true}
        {"type": "MULTI_SELECT",  "selected_option_ids": ["A", "B"]}
        {"type": "DRAG_TO_ORDER", "ordered_item_ids": ["c", "a", "b"]}
        {"type": "SCENARIO",      "responses": [{"sub_question_id": "s1", "answer": {...}}]}

    The learner's own answer is safe to coach with — it is theirs, and §11 names it explicitly. It
    is described to the coach *without any correctness annotation*: saying which two of their four
    multi-select ticks were right would hand over half the answer key (§12).
    """

    question_id: str
    answered: bool = False
    response: Mapping[str, Any] | None = None
    saved_at: str | None = None

    @property
    def has_response(self) -> bool:
        return self.answered and bool(self.response)


@runtime_checkable
class AttemptProvider(Protocol):
    """Read-only port onto UC-03.

    A transient failure must raise
    ``app.modules.coaching.domain.errors.UpstreamProviderUnavailableError``, so coaching is refused
    with a controlled, retryable state rather than proceeding with a half-read attempt.
    """

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        """Return the attempt, or ``None`` when it does not exist."""
        ...

    async def get_delivered_questions(self, attempt_id: str) -> tuple[DeliveredQuestion, ...]:
        """The paper as delivered, in delivery order."""
        ...

    async def get_learner_answers(self, attempt_id: str) -> tuple[LearnerAnswer, ...]:
        """Every answer recorded for the attempt. Unanswered questions may be omitted entirely."""
        ...
