"""Persistence for UC-06.

A ``Protocol`` plus today's SQLAlchemy implementation, as in every other capability here.

The transitions are compare-and-set:

* ``insert_pending`` leans on ``uq_qf_feedback_reports_attempt_id``, so two concurrent generations
  produce one report and the loser adopts it;
* ``mark_generated`` carries ``WHERE status <> 'GENERATED'``, so a slower run cannot overwrite a
  report that has already been frozen -- and the trigger would refuse it even if it tried.

Items are deleted and re-inserted rather than updated, because ``trg_qf_item_no_update`` makes
editing one impossible. Only ever done for a report that is still ``PENDING``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.feedback.domain.enums import ReportStatus
from app.modules.feedback.models import FeedbackItemRow, FeedbackReportRow


class FeedbackRepository(Protocol):
    """What the feedback service needs from persistence."""

    def get_by_attempt(self, attempt_id: str) -> FeedbackReportRow | None: ...

    def list_for_learner(
        self, learner_id: str, *, quiz_id: str | None = None
    ) -> list[FeedbackReportRow]: ...

    def list_items(self, report_id: str) -> list[FeedbackItemRow]: ...

    def insert_pending(self, **fields: Any) -> FeedbackReportRow: ...

    def record_run(self, report_id: str, now: datetime) -> None: ...

    def replace_items(
        self, report_id: str, rows: Sequence[dict[str, Any]], *, now: datetime
    ) -> list[FeedbackItemRow]: ...

    def mark_generated(self, report_id: str, *, now: datetime, **summary: Any) -> bool: ...

    def mark_failure(
        self,
        report_id: str,
        *,
        status: ReportStatus,
        failure_code: str,
        failure_message: str,
        now: datetime,
    ) -> bool: ...


class SqlAlchemyFeedbackRepository:
    """Today's implementation: SQLAlchemy over the shared metadata."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- reads ------------------------------------------------------------

    def get_by_attempt(self, attempt_id: str) -> FeedbackReportRow | None:
        return self._session.scalar(
            select(FeedbackReportRow).where(FeedbackReportRow.attempt_id == attempt_id)
        )

    def list_for_learner(
        self, learner_id: str, *, quiz_id: str | None = None
    ) -> list[FeedbackReportRow]:
        statement = select(FeedbackReportRow).where(FeedbackReportRow.learner_id == str(learner_id))
        if quiz_id is not None:
            statement = statement.where(FeedbackReportRow.quiz_id == str(quiz_id))
        return list(
            self._session.scalars(statement.order_by(FeedbackReportRow.attempt_number.desc())).all()
        )

    def list_items(self, report_id: str) -> list[FeedbackItemRow]:
        return list(
            self._session.scalars(
                select(FeedbackItemRow)
                .where(FeedbackItemRow.report_id == report_id)
                .order_by(FeedbackItemRow.position)
            ).all()
        )

    # ---- writes -----------------------------------------------------------

    def insert_pending(self, **fields: Any) -> FeedbackReportRow:
        row = FeedbackReportRow(status=ReportStatus.PENDING.value, **fields)
        self._session.add(row)
        self._session.flush()
        return row

    def record_run(self, report_id: str, now: datetime) -> None:
        self._session.execute(
            update(FeedbackReportRow)
            .where(
                FeedbackReportRow.id == report_id,
                FeedbackReportRow.status != ReportStatus.GENERATED.value,
            )
            .values(
                generation_attempt_count=FeedbackReportRow.generation_attempt_count + 1,
                updated_at=now,
            )
        )

    def replace_items(
        self, report_id: str, rows: Sequence[dict[str, Any]], *, now: datetime
    ) -> list[FeedbackItemRow]:
        for existing in self.list_items(report_id):
            self._session.delete(existing)
        self._session.flush()

        created = [FeedbackItemRow(report_id=report_id, created_at=now, **row) for row in rows]
        self._session.add_all(created)
        self._session.flush()
        return created

    def mark_generated(self, report_id: str, *, now: datetime, **summary: Any) -> bool:
        outcome = self._session.execute(
            update(FeedbackReportRow)
            .where(
                FeedbackReportRow.id == report_id,
                FeedbackReportRow.status != ReportStatus.GENERATED.value,
            )
            .values(
                status=ReportStatus.GENERATED.value,
                generated_at=now,
                updated_at=now,
                failure_code=None,
                failure_message=None,
                **summary,
            )
        )
        return bool(outcome.rowcount)

    def mark_failure(
        self,
        report_id: str,
        *,
        status: ReportStatus,
        failure_code: str,
        failure_message: str,
        now: datetime,
    ) -> bool:
        outcome = self._session.execute(
            update(FeedbackReportRow)
            .where(
                FeedbackReportRow.id == report_id,
                FeedbackReportRow.status != ReportStatus.GENERATED.value,
            )
            .values(
                status=status.value,
                failure_code=failure_code,
                failure_message=failure_message,
                updated_at=now,
            )
        )
        return bool(outcome.rowcount)
