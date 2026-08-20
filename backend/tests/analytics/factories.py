"""Deterministic builders for test data.

No randomness anywhere: every value is derived from an index, so a failing test
reproduces exactly and CSV determinism can be asserted byte for byte.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.modules.analytics.domain.enums import (
    AssessmentType,
    AttemptStatus,
    FlagReason,
    FlagStatus,
    ReportingQuestionType,
    ReviewActionType,
)
from app.modules.analytics.domain.records import (
    AttemptRecord,
    QuestionFlagRecord,
    QuestionMetadata,
    ResponseRecord,
    ReviewActionRecord,
)

BASE_TIME = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def make_attempt(
    attempt_id: str = "attempt-1",
    *,
    course_id: str = "course-1",
    learner_id: str = "learner-1",
    cohort_id: str | None = "cohort-a",
    assessment_type: AssessmentType | str = AssessmentType.STANDARD_QUIZ,
    status: AttemptStatus | str = AttemptStatus.COMPLETED,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    score: float | None = 75.0,
    passed: bool | None = True,
    **extra: Any,
) -> AttemptRecord:
    started = started_at or BASE_TIME
    if completed_at is None and str(getattr(status, "value", status)) == "COMPLETED":
        completed_at = started + timedelta(minutes=20)
    return AttemptRecord(
        attempt_id=attempt_id,
        course_id=course_id,
        learner_id=learner_id,
        cohort_id=cohort_id,
        assessment_type=assessment_type,
        status=status,
        started_at=started,
        completed_at=completed_at,
        score=score,
        passed=passed,
        **extra,
    )


def make_response(
    response_id: str = "response-1",
    *,
    attempt_id: str = "attempt-1",
    question_id: str = "question-1",
    selected_answer: str | None = "A",
    is_correct: bool | None = True,
    time_spent_seconds: float | None = 30.0,
    answered_at: datetime | None = None,
) -> ResponseRecord:
    return ResponseRecord(
        response_id=response_id,
        attempt_id=attempt_id,
        question_id=question_id,
        selected_answer=selected_answer,
        is_correct=is_correct,
        time_spent_seconds=time_spent_seconds,
        answered_at=answered_at if answered_at is not None else BASE_TIME,
    )


def make_question(
    question_id: str = "question-1",
    *,
    question_type: ReportingQuestionType | str = ReportingQuestionType.MULTIPLE_CHOICE,
    course_id: str | None = "course-1",
    text: str | None = "Sample question text",
    active: bool = True,
) -> QuestionMetadata:
    return QuestionMetadata(
        question_id=question_id,
        question_type=question_type,
        course_id=course_id,
        text=text,
        active=active,
    )


def make_flag(
    question_id: str = "question-1",
    *,
    status: FlagStatus = FlagStatus.FLAGGED,
    wrong_answer_rate: float = 80.0,
    threshold_used: float = 40.0,
    graded_responses_at_flag: int = 10,
    flagged_at: datetime | None = None,
    flagged_by: str = "system",
    resolved_at: datetime | None = None,
    resolved_by: str | None = None,
    resolution_action: ReviewActionType | None = None,
    reason: FlagReason = FlagReason.WRONG_ANSWER_RATE_EXCEEDED,
) -> QuestionFlagRecord:
    return QuestionFlagRecord(
        question_id=question_id,
        status=status,
        reason=reason,
        wrong_answer_rate=wrong_answer_rate,
        threshold_used=threshold_used,
        graded_responses_at_flag=graded_responses_at_flag,
        flagged_at=flagged_at or BASE_TIME,
        flagged_by=flagged_by,
        resolved_at=resolved_at,
        resolved_by=resolved_by,
        resolution_action=resolution_action,
    )


def make_action(
    action_id: str = "action-1",
    *,
    question_id: str = "question-1",
    action: ReviewActionType = ReviewActionType.NO_CHANGE,
    admin_id: str = "admin-1",
    created_at: datetime | None = None,
    note: str | None = None,
) -> ReviewActionRecord:
    return ReviewActionRecord(
        action_id=action_id,
        question_id=question_id,
        action=action,
        admin_id=admin_id,
        created_at=created_at or NOW,
        note=note,
    )


def build_dataset(
    *,
    attempts: int,
    questions_per_attempt: int = 4,
    courses: Sequence[str] = ("course-1", "course-2"),
    cohorts: Sequence[str] = ("cohort-a", "cohort-b"),
    learners: int = 50,
    start: datetime = BASE_TIME,
) -> tuple[list[AttemptRecord], list[ResponseRecord], list[QuestionMetadata]]:
    """Build a deterministic dataset spanning courses, cohorts and both assessment types.

    Every characteristic cycles on a different modulus so that filters, statuses
    and grading outcomes are independent of one another - a dataset where every
    'wrong' answer also happened to be in one cohort would hide filter bugs.
    """
    attempt_records: list[AttemptRecord] = []
    response_records: list[ResponseRecord] = []
    question_ids = [f"question-{i + 1}" for i in range(questions_per_attempt * 2)]

    for index in range(attempts):
        started = start + timedelta(hours=index)
        status = (
            AttemptStatus.COMPLETED
            if index % 5 != 4
            else (AttemptStatus.IN_PROGRESS if index % 10 == 4 else AttemptStatus.ABANDONED)
        )
        completed = status is AttemptStatus.COMPLETED
        score = float(40 + (index % 61)) if completed else None
        passed = (score >= 50.0) if score is not None else None

        attempt = AttemptRecord(
            attempt_id=f"attempt-{index:05d}",
            course_id=courses[index % len(courses)],
            learner_id=f"learner-{index % learners:04d}",
            cohort_id=cohorts[index % len(cohorts)],
            assessment_type=(
                AssessmentType.STANDARD_QUIZ
                if index % 3 != 0
                else AssessmentType.FORMAL_ASSESSMENT
            ),
            status=status,
            started_at=started,
            completed_at=started + timedelta(minutes=25) if completed else None,
            score=score,
            passed=passed,
        )
        attempt_records.append(attempt)

        for slot in range(questions_per_attempt):
            question_id = question_ids[(index + slot) % len(question_ids)]
            marker = (index + slot) % 7
            if marker == 6:
                is_correct: bool | None = None  # ungraded
                answer: str | None = None
            elif marker in (0, 1, 2, 3):
                is_correct = True
                answer = "A"
            else:
                is_correct = False
                answer = "B" if marker == 4 else "C"
            response_records.append(
                ResponseRecord(
                    response_id=f"response-{index:05d}-{slot}",
                    attempt_id=attempt.attempt_id,
                    question_id=question_id,
                    selected_answer=answer,
                    is_correct=is_correct,
                    time_spent_seconds=None if marker == 5 else float(15 + marker * 5),
                    answered_at=started + timedelta(minutes=slot),
                )
            )

    questions = [
        make_question(
            question_id,
            question_type=list(ReportingQuestionType)[i % len(ReportingQuestionType)],
            course_id=courses[i % len(courses)],
        )
        for i, question_id in enumerate(question_ids)
    ]
    return attempt_records, response_records, questions


def ids(records: Iterable[Any]) -> list[str]:
    """Helper for readable assertions."""
    return [getattr(r, "question_id", getattr(r, "attempt_id", "")) for r in records]
