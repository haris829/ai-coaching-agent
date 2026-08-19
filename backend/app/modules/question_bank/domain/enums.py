"""Question Bank vocabulary and per-type rules.

The **vocabulary** — ``QuestionType``, ``QuestionStatus`` and their labels — is shared with UC-01
and lives in :mod:`app.core.question_types`. It is re-exported here so this module remains the one
place
question-bank code looks for any enum, and so callers do not have to know which names happen to be
shared.

Everything else in this file is the question bank's own **policy**: how many options each
type needs,
which scoring strategies it may declare, which statuses are deliverable. UC-01 has no business
holding opinions about any of that, which is why it stays here rather than in the shared kernel.

The enums are ``str`` enums rather than native database enums so that:

* the schema stays portable across SQLite / PostgreSQL / MySQL / SQL Server, and
* adding a question type later is a code change, not a database migration.

The backend validation layer is authoritative: nothing reaches the database without passing through
these vocabularies.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.coercion import enum_values, parse_enum
from app.core.question_types import (
    QUESTION_TYPE_LABELS,
    QUESTION_TYPE_ORDER,
    QuestionStatus,
    QuestionType,
)

__all__ = [
    "ALLOWED_SCORING_STRATEGIES",
    "DELIVERABLE_STATUSES",
    "DRAG_TO_ORDER_MIN_ITEMS",
    "MAX_EXPLANATION_LENGTH",
    "MAX_OPTIONS_PER_QUESTION",
    "MAX_OPTION_TEXT_LENGTH",
    "MAX_POINTS",
    "MAX_QUESTION_TEXT_LENGTH",
    "MAX_SCENARIO_TEXT_LENGTH",
    "MULTI_SELECT_MIN_OPTIONS",
    "QUESTION_TYPE_LABELS",
    "QUESTION_TYPE_ORDER",
    "SCENARIO_MIN_LENGTH",
    "SCENARIO_MIN_OPTIONS",
    "SINGLE_CHOICE_OPTION_COUNT",
    "TRUE_FALSE_LABELS",
    "AttemptStatus",
    "Difficulty",
    "ImportStatus",
    "QuestionStatus",
    "QuestionType",
    "ScoringStrategy",
    "enum_values",
    "parse_enum",
    "uses_correct_order",
]


class ScoringStrategy(StrEnum):
    #: Full marks only for a completely correct response.
    ALL_OR_NOTHING = "ALL_OR_NOTHING"
    #: Marks pro-rata for the correct part of the response.
    PARTIAL_CREDIT = "PARTIAL_CREDIT"
    #: Pro-rata marks, minus ``penalty_per_incorrect`` for each incorrect selection.
    PARTIAL_CREDIT_WITH_PENALTY = "PARTIAL_CREDIT_WITH_PENALTY"


class Difficulty(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class AttemptStatus(StrEnum):
    """Status of one *question's* usage within an attempt.

    Distinct from ``quiz_configuration``'s attempt status, which is the state of the attempt itself.
    """

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


class ImportStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Delivery eligibility
# ---------------------------------------------------------------------------

#: Only ACTIVE questions may be handed to a future quiz. DRAFT is not yet publishable and RETIRED is
#: deliberately withheld while remaining fully readable for history.
DELIVERABLE_STATUSES: frozenset[QuestionStatus] = frozenset({QuestionStatus.ACTIVE})


# ---------------------------------------------------------------------------
# Per-type rules
# ---------------------------------------------------------------------------

#: Which scoring strategies each question type is allowed to declare.
ALLOWED_SCORING_STRATEGIES: dict[QuestionType, tuple[ScoringStrategy, ...]] = {
    QuestionType.SINGLE_CHOICE: (ScoringStrategy.ALL_OR_NOTHING,),
    QuestionType.TRUE_FALSE: (ScoringStrategy.ALL_OR_NOTHING,),
    QuestionType.SCENARIO: (ScoringStrategy.ALL_OR_NOTHING,),
    QuestionType.MULTI_SELECT: (
        ScoringStrategy.ALL_OR_NOTHING,
        ScoringStrategy.PARTIAL_CREDIT,
        ScoringStrategy.PARTIAL_CREDIT_WITH_PENALTY,
    ),
    QuestionType.DRAG_TO_ORDER: (
        ScoringStrategy.ALL_OR_NOTHING,
        ScoringStrategy.PARTIAL_CREDIT,
    ),
}

#: Fixed option labels for TRUE_FALSE questions.
TRUE_FALSE_LABELS: tuple[str, str] = ("TRUE", "FALSE")

#: Single-choice questions must present exactly this many options (UC-02 §9).
SINGLE_CHOICE_OPTION_COUNT = 4

#: Multi-select needs a real choice set to be meaningful.
MULTI_SELECT_MIN_OPTIONS = 3

#: Scenario questions need at least a genuine choice.
SCENARIO_MIN_OPTIONS = 2

#: Ordering questions need at least this many items for an order to exist.
DRAG_TO_ORDER_MIN_ITEMS = 2

#: A scenario vignette should be more than a sentence fragment.
SCENARIO_MIN_LENGTH = 20

MAX_QUESTION_TEXT_LENGTH = 5_000
MAX_SCENARIO_TEXT_LENGTH = 20_000
MAX_EXPLANATION_LENGTH = 5_000
MAX_OPTION_TEXT_LENGTH = 2_000
MAX_OPTIONS_PER_QUESTION = 26
MAX_POINTS = 1_000


def uses_correct_order(question_type: QuestionType) -> bool:
    """True when correctness is expressed by ``correct_position`` rather than ``is_correct``."""
    return question_type is QuestionType.DRAG_TO_ORDER
