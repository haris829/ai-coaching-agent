"""UC-10's own two tables — the module's entire write surface.

``in_memory.py`` remains the reference implementation and what the analytics tests run against;
this is what the merged application binds. Neither the services nor the domain know which one they
were given.

**Everything writable by UC-10 is in this file, and it is two things:** which questions are flagged
for content review, and the append-only record of what an administrator decided about them. Nothing
here can touch an attempt, an answer, a score or an outcome — the assessment side is a separate
class with no mutating method at all
(``integration/assessment_repository.py``).

**A flag clears only through a review action.** ``record_action`` and ``upsert_flag`` are separate
methods because the service calls them together in one unit of work: the action is what caused the
transition, and a flag state with no action explaining it is exactly what the audit requirement
forbids. The database backs that up — ``qy_review_actions`` is append-only by trigger, so an
administrator's decision cannot be quietly rewritten or removed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.logging import get_logger
from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.enums import (
    FlagReason,
    FlagStatus,
    ReviewActionType,
)
from app.modules.analytics.domain.records import QuestionFlagRecord
from app.modules.analytics.domain.review import ReviewActionRecord
from app.modules.analytics.errors import RepositoryUnavailableError, ReviewConflictError
from app.modules.analytics.models import QuestionFlagRow, ReviewActionRow
from app.modules.analytics.repositories.base import ReviewRepository

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def flag_from_row(row: QuestionFlagRow) -> QuestionFlagRecord:
    """One row as the immutable domain record the services work with.

    Public because the assessment repository reads flags too — a question's analytics and its flag
    state are shown together — and both sides must read them the same way. A second translation
    would be a second opinion about what ``RESOLVED`` means.
    """
    return QuestionFlagRecord(
        question_id=row.question_id,
        status=FlagStatus(row.status),
        reason=FlagReason(row.reason),
        wrong_answer_rate=row.wrong_answer_rate,
        threshold_used=row.threshold_used,
        graded_responses_at_flag=row.graded_responses_at_flag,
        flagged_at=row.flagged_at,
        flagged_by=row.flagged_by,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
        resolution_action=(
            ReviewActionType(row.resolution_action) if row.resolution_action else None
        ),
        updated_at=row.updated_at,
    )


def action_from_row(row: ReviewActionRow) -> ReviewActionRecord:
    return ReviewActionRecord(
        action_id=row.id,
        question_id=row.question_id,
        action=ReviewActionType(row.action),
        admin_id=row.admin_id,
        created_at=row.created_at,
        note=row.note,
        previous_flag_status=(
            FlagStatus(row.previous_flag_status) if row.previous_flag_status else None
        ),
        resulting_flag_status=(
            FlagStatus(row.resulting_flag_status) if row.resulting_flag_status else None
        ),
    )


# ---------------------------------------------------------------------------
# The review store
# ---------------------------------------------------------------------------


class SqlAlchemyReviewRepository(ReviewRepository):
    """``ReviewRepository`` over ``qy_question_flags`` and ``qy_review_actions``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- reads --------------------------------------------------------------

    async def get_flags(
        self,
        question_ids: Sequence[str],
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        context.raise_if_stopped()
        return await offload(self._get_flags, question_ids, statuses)

    async def get_flag(
        self, question_id: str, context: QueryContext
    ) -> QuestionFlagRecord | None:
        flags = await self.get_flags([question_id], context)
        return flags.get(question_id)

    async def list_actions(
        self,
        context: QueryContext,
        *,
        question_id: str | None = None,
        admin_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Sequence[ReviewActionRecord], int]:
        context.raise_if_stopped()
        return await offload(self._list_actions, question_id, admin_id, limit, offset)

    # ---- the two writes -----------------------------------------------------

    async def upsert_flag(
        self, flag: QuestionFlagRecord, context: QueryContext
    ) -> QuestionFlagRecord:
        context.raise_if_stopped()
        return await offload(self._upsert_flag, flag)

    async def record_action(
        self, action: ReviewActionRecord, context: QueryContext
    ) -> ReviewActionRecord:
        context.raise_if_stopped()
        return await offload(self._record_action, action)

    # ---- synchronous bodies -------------------------------------------------

    def _get_flags(
        self, question_ids: Sequence[str], statuses: Sequence[FlagStatus] | None
    ) -> Mapping[str, QuestionFlagRecord]:
        if not question_ids:
            return {}
        query = select(QuestionFlagRow).where(
            QuestionFlagRow.question_id.in_(list(question_ids))
        )
        if statuses is not None:
            query = query.where(
                QuestionFlagRow.status.in_([status.value for status in statuses])
            )
        try:
            rows = self._session.scalars(query).all()
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "The review store could not be read.", cause=exc
            ) from exc
        return {row.question_id: flag_from_row(row) for row in rows}

    def _list_actions(
        self, question_id: str | None, admin_id: str | None, limit: int, offset: int
    ) -> tuple[Sequence[ReviewActionRecord], int]:
        query = select(ReviewActionRow)
        count_query = select(func.count()).select_from(ReviewActionRow)
        if question_id is not None:
            query = query.where(ReviewActionRow.question_id == question_id)
            count_query = count_query.where(ReviewActionRow.question_id == question_id)
        if admin_id is not None:
            query = query.where(ReviewActionRow.admin_id == admin_id)
            count_query = count_query.where(ReviewActionRow.admin_id == admin_id)

        try:
            # Newest first: an audit log is read to find out what just happened.
            rows = self._session.scalars(
                query.order_by(ReviewActionRow.created_at.desc(), ReviewActionRow.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            total = self._session.scalar(count_query)
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "The review audit log could not be read.", cause=exc
            ) from exc
        return tuple(action_from_row(row) for row in rows), int(total or 0)

    def _upsert_flag(self, flag: QuestionFlagRecord) -> QuestionFlagRecord:
        """Create or replace the flag state for one question. Idempotent for an unchanged record.

        The question id is the primary key, so there is exactly one current state per question and
        no idempotency token is needed: writing the same state twice is the same row twice.
        """
        try:
            row = self._session.get(QuestionFlagRow, flag.question_id)
            if row is None:
                row = QuestionFlagRow(question_id=flag.question_id)
                self._session.add(row)

            row.status = flag.status.value
            row.reason = flag.reason.value
            row.wrong_answer_rate = flag.wrong_answer_rate
            row.threshold_used = flag.threshold_used
            row.graded_responses_at_flag = flag.graded_responses_at_flag
            row.flagged_at = flag.flagged_at
            row.flagged_by = flag.flagged_by
            row.resolved_at = flag.resolved_at
            row.resolved_by = flag.resolved_by
            row.resolution_action = (
                flag.resolution_action.value if flag.resolution_action else None
            )
            row.updated_at = flag.updated_at
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            # A CHECK refused the state — a resolved flag with no resolver, most likely. Reported
            # as a conflict rather than a persistence fault: the record is wrong, not the store.
            raise ReviewConflictError(
                "The flag state is not valid.",
                details={"question_id": flag.question_id},
                cause=exc,
            ) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise RepositoryUnavailableError(
                "The flag could not be saved.", cause=exc
            ) from exc
        return flag_from_row(row)

    def _record_action(self, action: ReviewActionRecord) -> ReviewActionRecord:
        """Append one review decision. Never updates, never deletes.

        A duplicate ``action_id`` is a caller bug and raises, as the protocol requires: two
        different decisions sharing an id would make the audit trail ambiguous about which one
        happened, and silently keeping the first would hide the second.
        """
        row = ReviewActionRow(
            id=action.action_id,
            question_id=action.question_id,
            action=action.action.value,
            admin_id=action.admin_id,
            created_at=action.created_at,
            note=action.note,
            previous_flag_status=(
                action.previous_flag_status.value if action.previous_flag_status else None
            ),
            resulting_flag_status=(
                action.resulting_flag_status.value if action.resulting_flag_status else None
            ),
        )
        try:
            self._session.add(row)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise ReviewConflictError(
                "A review action with this identifier has already been recorded.",
                details={"action_id": action.action_id},
                cause=exc,
            ) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise RepositoryUnavailableError(
                "The review action could not be recorded.", cause=exc
            ) from exc
        return action_from_row(row)
