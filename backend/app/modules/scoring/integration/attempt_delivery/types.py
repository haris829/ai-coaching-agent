"""UC-03 (Quiz Attempt Delivery) — contract types consumed by UC-04. Narrow by design. UC-04 does
not own attempts, does not create them, does not time them and never writes to them: it reads a
*submitted* attempt, its locked configuration snapshot and its frozen questions and answers, then
records a result of its own. Every field here is something UC-03 has already frozen. That is what
makes a score reproducible: re-running scoring a year later, after the question bank has been
edited ten times, reads exactly the same inputs. Mirrors the shape UC-03 itself uses for UC-01
and UC-02 — a contract type per boundary, with the translation confined to the adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.modules.scoring.domain.enums import QuestionType


@dataclass(frozen=True, slots=True)
class DeliveredOption:
    """One option exactly as the learner saw it, in the order they saw it."""

    option_id: str
    text: str
    #: The answer key frozen onto the attempt. ``None`` when the snapshot carried none.
    is_correct: bool | None = None
    #: DRAG_TO_ORDER only: 1-based rank in the correct sequence, independent of presented order.
    correct_position: int | None = None


@dataclass(frozen=True, slots=True)
class DeliveredQuestion:
    """One frozen question of a submitted attempt, with the learner's final answer."""

    attempt_question_id: str
    question_id: str
    question_version: int
    question_type: QuestionType
    position: int
    #: Marks this question was worth *in this attempt*. Frozen by UC-03 at delivery.
    max_marks: float
    prompt: str = ""
    scenario_text: str | None = None
    #: Choice options, or the orderable items for DRAG_TO_ORDER, in delivered order.
    options: tuple[DeliveredOption, ...] = ()
    #: SCENARIO: the sub-question ids UC-03 delivered, in order. UC-02's scenario maps to one.
    sub_question_ids: tuple[str, ...] = ()
    #: True when UC-03 recorded a saved answer for this question.
    answered: bool = False
    #: True when UC-03 considered the saved answer a complete response.
    complete: bool = False
    #: UC-03's canonical answer payload, or ``None`` when unanswered/cleared.
    response: dict[str, Any] | None = None
    #: Whatever else the frozen snapshot carried. Retained so nothing is silently dropped.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubmittedAttempt:
    """A submitted attempt, ready to be scored."""

    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_number: int
    status: str
    #: True when UC-03 has committed the attempt and frozen its answers -- the only state in which
    #: scoring is allowed. Resolved by the adapter, so UC-04 never has to know UC-03's status names.
    locked: bool
    configuration_version_id: str
    configuration_version_number: int
    #: Pass mark of the attempt's *own* configuration version, never the quiz's latest.
    pass_mark_percentage: float
    started_at: datetime
    submitted_at: datetime | None
    questions: tuple[DeliveredQuestion, ...] = ()
    #: The id of the successful submission, when UC-03 has one.
    submission_id: str | None = None
    #: The attempt's locked configuration snapshot, verbatim.
    configuration_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def time_taken_seconds(self) -> int | None:
        """Wall-clock seconds from start to submission, from UC-03's server-authoritative stamps."""
        if self.submitted_at is None:
            return None
        return max(0, int((self.submitted_at - self.started_at).total_seconds()))
