"""Canonical in-memory shapes for the Question Bank domain.

``QuestionDraft`` is the normalised, not-yet-validated form that BOTH entry points produce:
the JSON admin API and the CSV bulk importer. Validation and persistence therefore behave
identically no matter where a question came from.

``ValidatedQuestion`` is the post-validation form: types narrowed, defaults applied, every
type-specific invariant guaranteed. Only this shape is ever persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.question_bank.domain.enums import (
    Difficulty,
    QuestionStatus,
    QuestionType,
    ScoringStrategy,
)


@dataclass(slots=True)
class OptionDraft:
    """An answer option (choice types) or an orderable item (DRAG_TO_ORDER)."""

    label: Any = None
    text: Any = None
    #: Authoring / default presentation order, 1-based. Assigned by index when omitted.
    position: Any = None
    #: Choice types only.
    is_correct: Any = None
    #: SCENARIO only — the single primary answer.
    is_primary: Any = None
    #: DRAG_TO_ORDER only — 1-based rank in the CORRECT ANSWER ORDER.
    correct_position: Any = None
    feedback: Any = None


@dataclass(slots=True)
class ScoringDraft:
    points: Any = None
    scoring_strategy: Any = None
    penalty_per_incorrect: Any = None


@dataclass(slots=True)
class QuestionDraft:
    type: Any = None
    status: Any = None
    question_text: Any = None
    scenario_text: Any = None
    explanation: Any = None
    difficulty: Any = None
    external_ref: Any = None
    options: list[OptionDraft] = field(default_factory=list)
    #: Topic names. Resolved to `Topic` rows (created on demand) during persistence.
    topics: list[Any] = field(default_factory=list)
    #: Topic ids, when the caller already knows them (admin UI select).
    topic_ids: list[Any] = field(default_factory=list)
    scoring: ScoringDraft = field(default_factory=ScoringDraft)


@dataclass(slots=True)
class ValidatedOption:
    label: str
    text: str
    #: Default presentation order. Delivery may shuffle this; it is NOT the answer.
    position: int
    is_correct: bool
    is_primary: bool
    #: Correct answer order for DRAG_TO_ORDER. Independent of ``position``.
    correct_position: int | None
    feedback: str | None


@dataclass(slots=True)
class ValidatedQuestion:
    type: QuestionType
    status: QuestionStatus
    question_text: str
    scenario_text: str | None
    explanation: str | None
    difficulty: Difficulty | None
    external_ref: str | None
    points: float
    scoring_strategy: ScoringStrategy
    penalty_per_incorrect: float
    options: list[ValidatedOption]
    topic_names: list[str]
    topic_ids: list[str]

    @property
    def correct_labels(self) -> list[str]:
        """Correct answer labels for choice types (empty for DRAG_TO_ORDER)."""
        if self.type is QuestionType.DRAG_TO_ORDER:
            return []
        return [option.label for option in self.options if option.is_correct]

    @property
    def correct_order(self) -> list[str]:
        """Correct label sequence for DRAG_TO_ORDER (empty for choice types)."""
        if self.type is not QuestionType.DRAG_TO_ORDER:
            return []
        ordered = sorted(
            (o for o in self.options if o.correct_position is not None),
            key=lambda o: o.correct_position or 0,
        )
        return [option.label for option in ordered]

    @property
    def primary_label(self) -> str | None:
        """The primary answer label (SCENARIO), if one is marked."""
        for option in self.options:
            if option.is_primary:
                return option.label
        return None
