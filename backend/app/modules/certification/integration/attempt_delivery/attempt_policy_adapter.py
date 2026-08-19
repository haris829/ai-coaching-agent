"""UC-03's attempts, seen through UC-05's policy port.

Read-only: two ``SELECT``s -- the attempt row, and a ``COUNT`` of the learner's attempts at that
quiz -- plus a translation. The pass mark, the maximum attempts and the course and quiz names all
come out of the configuration snapshot frozen onto the attempt, never from a fresh read of UC-01.

The names live in the snapshot because UC-01's configuration adapter puts them there (``quizTitle``,
``courseTitle`` in ``extra``) for exactly this kind of downstream use. When they are absent -- an
attempt created before that was carried, or a course with no title -- the fallback is the course id,
so a certificate always has something truthful on it rather than an empty string.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.attempt_delivery.models import QuizAttempt
from app.modules.certification.integration.attempt_delivery.port import AttemptPolicy


class AttemptPolicyAdapter:
    """:class:`~...attempt_delivery.port.AttemptPolicyPort` over UC-03's tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_policy(self, attempt_id: str, *, learner_id: str | None = None) -> AttemptPolicy | None:
        statement = select(QuizAttempt).where(QuizAttempt.id == attempt_id)
        if learner_id is not None:
            # Ownership is part of the query, so no caller can forget to check it.
            statement = statement.where(QuizAttempt.learner_id == str(learner_id))
        attempt = self._session.scalar(statement)
        if attempt is None:
            return None

        attempts_used = int(
            self._session.scalar(
                select(func.count(QuizAttempt.id)).where(
                    QuizAttempt.learner_id == attempt.learner_id,
                    QuizAttempt.quiz_id == attempt.quiz_id,
                )
            )
            or 0
        )

        snapshot: dict[str, Any] = attempt.configuration_snapshot or {}
        extra: dict[str, Any] = snapshot.get("extra") or {}
        max_attempts = snapshot.get("maxAttempts")

        return AttemptPolicy(
            attempt_id=attempt.id,
            learner_id=attempt.learner_id,
            course_id=attempt.course_id,
            quiz_id=attempt.quiz_id,
            attempt_number=attempt.attempt_number,
            configuration_version_id=attempt.configuration_version_id,
            pass_mark_percentage=float(snapshot.get("passMarkPercentage") or 0.0),
            max_attempts=None if max_attempts is None else int(max_attempts),
            attempts_used=attempts_used,
            course_name=str(extra.get("courseTitle") or f"Course {attempt.course_id}"),
            quiz_title=extra.get("quizTitle") if isinstance(extra.get("quizTitle"), str) else None,
            submitted_at=attempt.submitted_at,
            started_at=attempt.started_at,
        )
