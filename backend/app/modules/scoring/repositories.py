"""Persistence for UC-04.

Two layers, deliberately: a ``Protocol`` describing what the service needs, and today's SQLAlchemy
implementation of it. The service depends on the protocol, so moving to the company database -- or
to a repository that talks to a different store entirely -- changes this file and nothing above it.

Every state change is a **compare-and-set**, not a read-then-write:

* ``insert_pending`` relies on ``uq_qr_attempt_results_attempt_id`` to decide a race, so two
  concurrent scoring runs produce one result row and the loser adopts the winner's;
* ``mark_scored`` and ``mark_pending_failure`` both carry ``WHERE status = 'PENDING_SCORE'``, so a
  run that lost the race changes nothing rather than overwriting a confirmed score.

Together with the database trigger on ``qr_attempt_results``, that makes "a confirmed score is never
modified" true under concurrency and not merely intended.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.scoring.domain.enums import ResultStatus
from app.modules.scoring.models import AttemptResult, QuestionScoreRow


class ResultRepository(Protocol):
    """What the scoring service needs from persistence."""

    def get_by_attempt(self, attempt_id: str) -> AttemptResult | None: ...

    def get(self, result_id: str) -> AttemptResult | None: ...

    def list_for_learner(
        self, learner_id: str, *, quiz_id: str | None = None
    ) -> list[AttemptResult]: ...

    def insert_pending(self, **fields: Any) -> AttemptResult: ...

    def record_run(self, result_id: str, now: datetime) -> None: ...

    def mark_scored(self, result_id: str, *, now: datetime, **totals: Any) -> bool: ...

    def mark_pending_failure(
        self,
        result_id: str,
        *,
        failure_code: str | None,
        failure_message: str | None,
        anomalies: Any,
        now: datetime,
        **totals: Any,
    ) -> bool: ...

    def replace_question_scores(
        self, result_id: str, rows: Sequence[dict[str, Any]], *, now: datetime
    ) -> list[QuestionScoreRow]: ...

    def list_question_scores(self, result_id: str) -> list[QuestionScoreRow]: ...


class SqlAlchemyResultRepository:
    """Today's implementation: SQLAlchemy over the shared metadata."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- reads ------------------------------------------------------------

    def get_by_attempt(self, attempt_id: str) -> AttemptResult | None:
        return self._session.scalar(
            select(AttemptResult).where(AttemptResult.attempt_id == attempt_id)
        )

    def get(self, result_id: str) -> AttemptResult | None:
        return self._session.get(AttemptResult, result_id)

    def list_for_learner(
        self, learner_id: str, *, quiz_id: str | None = None
    ) -> list[AttemptResult]:
        statement = select(AttemptResult).where(AttemptResult.learner_id == str(learner_id))
        if quiz_id is not None:
            statement = statement.where(AttemptResult.quiz_id == str(quiz_id))
        return list(
            self._session.scalars(statement.order_by(AttemptResult.attempt_number.desc())).all()
        )

    def list_question_scores(self, result_id: str) -> list[QuestionScoreRow]:
        return list(
            self._session.scalars(
                select(QuestionScoreRow)
                .where(QuestionScoreRow.result_id == result_id)
                .order_by(QuestionScoreRow.position)
            ).all()
        )

    # ---- writes -----------------------------------------------------------

    def insert_pending(self, **fields: Any) -> AttemptResult:
        """Claim the one result row for an attempt.

        Raises ``IntegrityError`` when another run already claimed it -- which the service resolves
        by adopting the winner rather than by reading first and hoping.
        """
        row = AttemptResult(status=ResultStatus.PENDING_SCORE.value, **fields)
        self._session.add(row)
        self._session.flush()
        return row

    def record_run(self, result_id: str, now: datetime) -> None:
        """Count a scoring run against a result that has not been confirmed."""
        self._session.execute(
            update(AttemptResult)
            .where(
                AttemptResult.id == result_id,
                AttemptResult.status == ResultStatus.PENDING_SCORE.value,
            )
            .values(scoring_attempt_count=AttemptResult.scoring_attempt_count + 1, updated_at=now)
        )

    def mark_scored(self, result_id: str, *, now: datetime, **totals: Any) -> bool:
        """Confirm the score. False when the result was already confirmed by another run."""
        outcome = self._session.execute(
            update(AttemptResult)
            .where(
                AttemptResult.id == result_id,
                AttemptResult.status == ResultStatus.PENDING_SCORE.value,
            )
            .values(
                status=ResultStatus.SCORED.value,
                scored_at=now,
                updated_at=now,
                failure_code=None,
                failure_message=None,
                anomalies=None,
                **totals,
            )
        )
        return bool(outcome.rowcount)

    def mark_pending_failure(
        self,
        result_id: str,
        *,
        failure_code: str | None,
        failure_message: str | None,
        anomalies: Any,
        now: datetime,
        **totals: Any,
    ) -> bool:
        """Record why a run could not confirm, leaving the result retryable."""
        outcome = self._session.execute(
            update(AttemptResult)
            .where(
                AttemptResult.id == result_id,
                AttemptResult.status == ResultStatus.PENDING_SCORE.value,
            )
            .values(
                failure_code=failure_code,
                failure_message=failure_message,
                anomalies=anomalies,
                updated_at=now,
                **totals,
            )
        )
        return bool(outcome.rowcount)

    def replace_question_scores(
        self, result_id: str, rows: Sequence[dict[str, Any]], *, now: datetime
    ) -> list[QuestionScoreRow]:
        """Write the per-question scores for a result.

        Rows are deleted and re-inserted rather than updated, because a question score is
        write-once: the database trigger rejects an ``UPDATE`` outright. Only ever reached for a
        result that is still ``PENDING_SCORE``, so no confirmed score's rows can be replaced.
        """
        existing = self.list_question_scores(result_id)
        for row in existing:
            self._session.delete(row)
        self._session.flush()

        created = [
            QuestionScoreRow(result_id=result_id, created_at=now, **fields) for fields in rows
        ]
        self._session.add_all(created)
        self._session.flush()
        return created
