"""Assembling a feedback report.

Pure functions over frozen data. Everything the report says is either copied from UC-04's frozen
score, copied from UC-05's outcome, resolved from the question bank's immutable version snapshot, or
one of the defined fallbacks. Nothing is computed from the live question bank and nothing at all is
generated. The per-question shape is fixed by the requirement: question · learner answer · correct
answer · explanation · question score · lesson reference with the multi-select addition of each
option's correct/incorrect status and its mark contribution -- which UC-04 already froze, so this
module renders it rather than recomputing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.feedback.domain.fallbacks import (
    NO_ANSWER_GIVEN,
    NO_CORRECT_ANSWER,
    NO_EXPLANATION,
    NO_LESSON_REFERENCE,
    NO_QUESTION_TEXT,
)
from app.modules.feedback.integration.certification.port import AttemptOutcomeSummary
from app.modules.feedback.integration.question_bank.port import QuestionContent
from app.modules.feedback.integration.scoring.port import ScoredAttempt, ScoredQuestion

#: Question types whose feedback must include the per-option breakdown. Multi-select is the type the
#: requirement names; the others carry it too when UC-04 froze one, because a uniform shape is
#: easier
#: for a client than a conditional one.
BREAKDOWN_REQUIRED_TYPES: frozenset[str] = frozenset({"MULTI_SELECT"})


@dataclass(frozen=True, slots=True)
class FeedbackItem:
    """One question's feedback."""

    position: int
    question_id: str
    question_version: int
    question_type: str
    question_text: str
    scenario_text: str | None
    learner_answer: dict[str, Any]
    correct_answer: dict[str, Any]
    explanation: str
    lesson_reference: str
    question_score: float
    maximum_marks: float
    deduction: float
    outcome: str
    answered: bool
    option_breakdown: tuple[dict[str, Any], ...] = ()
    question_reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "questionId": self.question_id,
            "questionVersion": self.question_version,
            "questionReference": self.question_reference,
            "questionType": self.question_type,
            "question": self.question_text,
            "scenarioText": self.scenario_text,
            "learnerAnswer": self.learner_answer,
            "correctAnswer": self.correct_answer,
            "explanation": self.explanation,
            "lessonReference": self.lesson_reference,
            "questionScore": self.question_score,
            "maximumMarks": self.maximum_marks,
            "deduction": self.deduction,
            "outcome": self.outcome,
            "answered": self.answered,
            "optionBreakdown": [dict(option) for option in self.option_breakdown],
        }


@dataclass(frozen=True, slots=True)
class FeedbackReport:
    """The whole report for one attempt."""

    attempt_id: str
    result_id: str
    outcome_id: str | None
    total_marks: float
    maximum_marks: float
    percentage: float
    #: ``None`` when pass/fail has not been determined yet -- reported as unknown, never guessed.
    passed: bool | None
    pass_mark_percentage: float
    time_taken_seconds: int | None
    total_questions: int
    correct_count: int
    incorrect_count: int
    unanswered_count: int
    items: tuple[FeedbackItem, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attemptId": self.attempt_id,
            "resultId": self.result_id,
            "outcomeId": self.outcome_id,
            "summary": {
                "totalScore": self.total_marks,
                "maximumMarks": self.maximum_marks,
                "percentage": self.percentage,
                "passed": self.passed,
                "passMarkPercentage": self.pass_mark_percentage,
                "timeTakenSeconds": self.time_taken_seconds,
                "totalQuestions": self.total_questions,
                "correctCount": self.correct_count,
                "incorrectCount": self.incorrect_count,
                "unansweredCount": self.unanswered_count,
            },
            "items": [item.to_dict() for item in self.items],
        }


def _answer_display(scored: ScoredQuestion) -> dict[str, Any]:
    """The learner's answer, with a stated fallback when there wasn't one."""
    answer = dict(scored.learner_answer or {})
    labels = answer.get("labels") or []
    if not scored.answered or not labels:
        return {**answer, "labels": list(labels), "summary": NO_ANSWER_GIVEN}
    return {**answer, "labels": list(labels), "summary": ", ".join(str(label) for label in labels)}


def _correct_display(scored: ScoredQuestion) -> dict[str, Any]:
    answer = dict(scored.correct_answer or {})
    labels = answer.get("labels") or []
    if not labels:
        return {**answer, "labels": [], "summary": NO_CORRECT_ANSWER}
    return {**answer, "labels": list(labels), "summary": ", ".join(str(label) for label in labels)}


def build_item(scored: ScoredQuestion, content: QuestionContent | None) -> FeedbackItem:
    """Assemble one question's feedback from its frozen score plus its authored content.

    The explanation is taken from the question bank's snapshot when it has one, then from whatever
    UC-04 captured at scoring time, and only then from the defined fallback. Both sources are frozen
    copies of the same authored text; preferring the bank's keeps the wording identical to the
    question's own history.
    """
    explanation = (
        (content.explanation if content is not None else None)
        or scored.explanation
        or NO_EXPLANATION
    )
    lesson = (content.lesson_reference if content is not None else None) or NO_LESSON_REFERENCE

    feedback_by_option = dict(content.option_feedback) if content is not None else {}
    breakdown = tuple(
        {
            **dict(option),
            # Authored per-option feedback, when the question has any. Absent rather than invented.
            **(
                {"feedback": feedback_by_option[str(option.get("optionId"))]}
                if str(option.get("optionId")) in feedback_by_option
                else {}
            ),
        }
        for option in scored.option_marks
    )

    return FeedbackItem(
        position=scored.position,
        question_id=scored.question_id,
        question_version=scored.question_version,
        question_type=scored.question_type,
        question_text=scored.question_text or NO_QUESTION_TEXT,
        scenario_text=scored.scenario_text,
        learner_answer=_answer_display(scored),
        correct_answer=_correct_display(scored),
        explanation=explanation,
        lesson_reference=lesson,
        question_score=scored.awarded_marks,
        maximum_marks=scored.maximum_marks,
        deduction=scored.deduction,
        outcome=scored.outcome,
        answered=scored.answered,
        option_breakdown=breakdown,
        question_reference=content.question_reference if content is not None else None,
    )


def build_report(
    scored: ScoredAttempt,
    outcome: AttemptOutcomeSummary | None,
    content: dict[tuple[str, int], QuestionContent],
) -> FeedbackReport:
    """Assemble the whole report.

    ``content`` is keyed by ``(question_id, version)``; a question with no entry still produces an
    item, using the fallbacks. Missing authored content degrades one field of one item -- it never
    costs the learner their feedback.
    """
    items = tuple(
        build_item(question, content.get((question.question_id, question.question_version)))
        for question in sorted(scored.questions, key=lambda item: item.position)
    )
    return FeedbackReport(
        attempt_id=scored.attempt_id,
        result_id=scored.result_id,
        outcome_id=outcome.outcome_id if outcome is not None else None,
        total_marks=scored.total_marks,
        maximum_marks=scored.maximum_marks,
        percentage=scored.percentage,
        passed=outcome.passed if outcome is not None else None,
        pass_mark_percentage=(
            outcome.pass_mark_percentage if outcome is not None else scored.pass_mark_percentage
        ),
        time_taken_seconds=scored.time_taken_seconds,
        total_questions=scored.total_questions,
        correct_count=scored.correct_count,
        incorrect_count=scored.incorrect_count,
        unanswered_count=scored.unanswered_count,
        items=items,
    )
