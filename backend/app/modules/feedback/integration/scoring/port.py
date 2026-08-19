"""The scored-attempt boundary. UC-06 reads its content from UC-04's *persisted* score rather than
from UC-03's attempt or UC-02's question bank. That is deliberate and it is the mechanism behind
the historical-consistency requirement: when UC-04 confirmed the score it froze the question
text, the learner's answer, the correct answer and the per-option marks onto each question-score
row. A report assembled from those rows says the same thing in five years' time as it does today,
whatever happens to the question afterwards. The only thing UC-06 asks UC-02 for is the
*authored* explanation and lesson reference, and that goes through its own port -- see
``feedback/integration/question_bank``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

__all__ = ["ScoredAttempt", "ScoredQuestion", "ScoreDetailPort"]


@dataclass(frozen=True, slots=True)
class ScoredQuestion:
    """One question's marks, as frozen by UC-04."""

    question_id: str
    question_version: int
    question_type: str
    position: int
    question_text: str
    scenario_text: str | None

    awarded_marks: float
    maximum_marks: float
    deduction: float
    outcome: str
    answered: bool

    #: Rendered forms UC-04 froze: option ids plus the texts the learner saw.
    learner_answer: dict[str, Any] = field(default_factory=dict)
    correct_answer: dict[str, Any] = field(default_factory=dict)
    #: Per option: ``{optionId, text, selected, correct, markContribution}``.
    option_marks: tuple[dict[str, Any], ...] = ()
    #: The explanation UC-04 captured from the answer key, when it had one.
    explanation: str | None = None
    #: Topic names frozen at snapshot time -- the raw material for a lesson reference.
    topics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScoredAttempt:
    """The confirmed score for one attempt, with its per-question detail."""

    result_id: str
    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_number: int
    configuration_version_id: str

    total_marks: float
    maximum_marks: float
    percentage: float
    pass_mark_percentage: float
    total_questions: int
    correct_count: int
    incorrect_count: int
    unanswered_count: int
    time_taken_seconds: int | None

    #: True only when UC-04 has confirmed the score. Feedback is never built from a pending one.
    confirmed: bool
    status: str
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    questions: tuple[ScoredQuestion, ...] = ()


class ScoreDetailPort(Protocol):
    """Read access to UC-04's confirmed scores and their per-question detail."""

    def get_scored_attempt(self, attempt_id: str) -> ScoredAttempt | None:
        """The score for an attempt, or ``None`` when UC-04 has recorded none."""
        ...
