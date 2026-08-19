"""Serialisation for UC-06.

A generated report is served from its stored ``payload`` -- the exact document that was built --
with the row's status and retry information wrapped around it. Rebuilding the JSON from the item
rows on every read would risk a subtle drift between what was generated and what is shown, which is
the one thing this capability must not allow.

A report that is not generated yet has no payload, so the response carries the status and the reason
instead. The learner's score and pass/fail are unaffected either way, and the response says so.
"""

from __future__ import annotations

from typing import Any

from app.core.time import iso_or_none
from app.modules.feedback.domain.enums import REPORT_STATUS_LABELS, ReportStatus
from app.modules.feedback.models import FeedbackItemRow, FeedbackReportRow


def status_label(status: str) -> str:
    try:
        return REPORT_STATUS_LABELS[ReportStatus(status)]
    except ValueError:  # pragma: no cover - the column has a CHECK constraint
        return status


def present_item(item: FeedbackItemRow) -> dict[str, Any]:
    """One item, from its own row. Used when a client wants the rows rather than the payload."""
    return {
        "position": item.position,
        "questionId": item.question_id,
        "questionVersion": item.question_version,
        "questionReference": item.question_reference,
        "questionType": item.question_type,
        "question": item.question_text,
        "scenarioText": item.scenario_text,
        "learnerAnswer": item.learner_answer or {},
        "correctAnswer": item.correct_answer or {},
        "explanation": item.explanation,
        "lessonReference": item.lesson_reference,
        "questionScore": item.question_score,
        "maximumMarks": item.maximum_marks,
        "deduction": item.deduction,
        "outcome": item.outcome,
        "answered": bool(item.answered),
        "optionBreakdown": item.option_breakdown or [],
    }


def present_report(report: FeedbackReportRow, items: list[FeedbackItemRow]) -> dict[str, Any]:
    return {
        "feedbackId": report.id,
        "attemptId": report.attempt_id,
        "resultId": report.result_id,
        "outcomeId": report.outcome_id,
        "status": report.status,
        "statusLabel": status_label(report.status),
        "summary": {
            "totalScore": report.total_marks,
            "maximumMarks": report.maximum_marks,
            "percentage": report.percentage,
            "passMarkPercentage": report.pass_mark_percentage,
            "passed": None if report.passed is None else bool(report.passed),
            "timeTakenSeconds": report.time_taken_seconds,
            "totalQuestions": report.total_questions,
            "correctCount": report.correct_count,
            "incorrectCount": report.incorrect_count,
            "unansweredCount": report.unanswered_count,
        },
        # The frozen document, when there is one. `items` is the same content row by row.
        "report": report.payload,
        "items": [present_item(item) for item in items],
        "generationAttemptCount": report.generation_attempt_count,
        "failureCode": report.failure_code,
        "failureMessage": report.failure_message,
        "generatedAt": iso_or_none(report.generated_at),
    }


def present_summary(report: FeedbackReportRow) -> dict[str, Any]:
    """A list row: enough to decide which report to open, without its whole payload."""
    return {
        "feedbackId": report.id,
        "attemptId": report.attempt_id,
        "attemptNumber": report.attempt_number,
        "quizId": report.quiz_id,
        "status": report.status,
        "statusLabel": status_label(report.status),
        "percentage": report.percentage,
        "passed": None if report.passed is None else bool(report.passed),
        "generatedAt": iso_or_none(report.generated_at),
    }
