"""UC-04's scores, seen through UC-06's port.

Read-only projection of ``qr_attempt_results`` and ``qr_question_scores``. The only file in UC-06
that knows UC-04's schema exists.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.feedback.integration.scoring.port import ScoredAttempt, ScoredQuestion
from app.modules.scoring.domain.enums import ResultStatus
from app.modules.scoring.models import AttemptResult, QuestionScoreRow


class ScoringDetailAdapter:
    """:class:`~...scoring.port.ScoreDetailPort` over UC-04's tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_scored_attempt(self, attempt_id: str) -> ScoredAttempt | None:
        result = self._session.scalar(
            select(AttemptResult).where(AttemptResult.attempt_id == attempt_id)
        )
        if result is None:
            return None

        rows = self._session.scalars(
            select(QuestionScoreRow)
            .where(QuestionScoreRow.result_id == result.id)
            .order_by(QuestionScoreRow.position)
        ).all()

        return ScoredAttempt(
            result_id=result.id,
            attempt_id=result.attempt_id,
            learner_id=result.learner_id,
            course_id=result.course_id,
            quiz_id=result.quiz_id,
            attempt_number=result.attempt_number,
            configuration_version_id=result.configuration_version_id,
            total_marks=float(result.total_marks),
            maximum_marks=float(result.maximum_marks),
            percentage=float(result.percentage),
            pass_mark_percentage=float(result.pass_mark_percentage),
            total_questions=result.total_questions,
            correct_count=result.correct_count,
            incorrect_count=result.incorrect_count,
            unanswered_count=result.unanswered_count,
            time_taken_seconds=result.time_taken_seconds,
            # UC-04 owns what "confirmed" means; UC-06 only reads the answer.
            confirmed=result.status == str(ResultStatus.SCORED),
            status=result.status,
            started_at=result.started_at,
            submitted_at=result.submitted_at,
            questions=tuple(_to_question(row) for row in rows),
        )


def _to_question(row: QuestionScoreRow) -> ScoredQuestion:
    return ScoredQuestion(
        question_id=row.question_id,
        question_version=row.question_version,
        question_type=row.question_type,
        position=row.position,
        question_text=row.question_text or "",
        scenario_text=row.scenario_text,
        awarded_marks=float(row.awarded_marks),
        maximum_marks=float(row.maximum_marks),
        deduction=float(row.deduction),
        outcome=row.outcome,
        answered=bool(row.answered),
        learner_answer=dict(row.learner_answer_display or {}),
        correct_answer=dict(row.correct_answer_display or {}),
        option_marks=tuple(row.option_marks or ()),
        explanation=row.explanation,
        topics=tuple(str(topic) for topic in (row.topics or ()) if topic),
    )
