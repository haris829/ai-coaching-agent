"""Serialisation for UC-04.

An allow-list, not an echo of the row. Two reasons it matters here rather than being a formality:

* a result carries the answer key's *shape* in ``correct_answer_display``, and that is legitimate
  after submission but must be a deliberate field rather than something a new column leaks into;
* the status label is defined once, so the API and the test UI cannot disagree about what "Submitted
  -- Pending Score" is called.
"""

from __future__ import annotations

from typing import Any

from app.core.time import iso_or_none
from app.modules.scoring.domain.enums import RESULT_STATUS_LABELS, ResultStatus
from app.modules.scoring.models import AttemptResult, QuestionScoreRow


def status_label(status: str) -> str:
    try:
        return RESULT_STATUS_LABELS[ResultStatus(status)]
    except ValueError:  # pragma: no cover - the column has a CHECK constraint
        return status


def present_result(result: AttemptResult) -> dict[str, Any]:
    return {
        "resultId": result.id,
        "attemptId": result.attempt_id,
        "submissionId": result.submission_id,
        "learnerId": result.learner_id,
        "courseId": result.course_id,
        "quizId": result.quiz_id,
        "attemptNumber": result.attempt_number,
        "status": result.status,
        "statusLabel": status_label(result.status),
        "totalMarks": result.total_marks,
        "maximumMarks": result.maximum_marks,
        "percentage": result.percentage,
        "passMarkPercentage": result.pass_mark_percentage,
        "totalQuestions": result.total_questions,
        "correctCount": result.correct_count,
        "incorrectCount": result.incorrect_count,
        "unansweredCount": result.unanswered_count,
        "timeTakenSeconds": result.time_taken_seconds,
        "startedAt": iso_or_none(result.started_at),
        "submittedAt": iso_or_none(result.submitted_at),
        "scoredAt": iso_or_none(result.scored_at),
        # The version the attempt ran under -- never the quiz's current one.
        "configurationVersionId": result.configuration_version_id,
        "configurationVersion": result.configuration_version_number,
        "anomalies": result.anomalies or [],
        "failureCode": result.failure_code,
        "failureMessage": result.failure_message,
        "scoringAttemptCount": result.scoring_attempt_count,
        "algorithmVersion": result.algorithm_version,
    }


def present_question_score(score: QuestionScoreRow) -> dict[str, Any]:
    return {
        "questionId": score.question_id,
        "questionVersion": score.question_version,
        "questionType": score.question_type,
        "position": score.position,
        "questionText": score.question_text,
        "scenarioText": score.scenario_text,
        "awardedMarks": score.awarded_marks,
        "maximumMarks": score.maximum_marks,
        "rawMarks": score.raw_marks,
        "deduction": score.deduction,
        "outcome": score.outcome,
        "answered": bool(score.answered),
        "learnerAnswer": score.learner_answer_display or {},
        "correctAnswer": score.correct_answer_display or {},
        "optionMarks": score.option_marks or [],
        "anomaly": score.anomaly,
        "answerKeySource": score.answer_key_source,
    }


def present_full(result: AttemptResult, scores: list[QuestionScoreRow]) -> dict[str, Any]:
    return {
        "result": present_result(result),
        "questionScores": [present_question_score(score) for score in scores],
    }
