"""Content-review actions (spec sections 11, 17, 18).

The only write path in UC-10, and the only way a flag ever leaves the
``FLAGGED`` state. Three decisions are supported: No Change, Question Updated,
Question Retired. Each produces an immutable audit entry recording the question,
the decision, the acting administrator and the timestamp.

Auditability rules
------------------

* Entries are append-only. Nothing here updates or deletes an existing entry, so
  the history of a question is the complete history.
* The administrator identity comes from the authenticated caller, never from the
  request body. A body that claims a different identity is rejected rather than
  quietly overridden, because a mismatch means the client is confused about who
  it is acting as.
* An action is recorded even when the question carries no flag - an administrator
  may retire or amend a question nobody flagged, and that decision is still
  worth auditing.
* Retirement is terminal. Further actions against a retired question are refused
  with a conflict rather than appended, so the audit log cannot imply that a
  withdrawn question came back.

This service never touches assessment data: it holds a reference to the review
repository only.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from app.core.logging import get_logger
from app.core.time import Clock, SystemClock
from app.modules.analytics.cancellation import QueryContext, run_with_deadline
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.domain.enums import FlagReason, FlagStatus, ReviewActionType
from app.modules.analytics.domain.records import QuestionFlagRecord, ReviewActionRecord
from app.modules.analytics.domain.review import (
    ReviewActionRequest,
    ReviewActionResponse,
    ReviewAuditPage,
    ReviewHistoryResponse,
)
from app.modules.analytics.errors import (
    AnalyticsError,
    AuthorizationError,
    RepositoryUnavailableError,
    ReviewConflictError,
)
from app.modules.analytics.repositories.base import ReviewRepository
from app.modules.analytics.services.aggregation import flag_summary_from_record

__all__ = ["ReviewService"]

logger = get_logger("review")


class ReviewService:
    """Records review decisions and transitions flags accordingly."""

    def __init__(
        self,
        review_repository: ReviewRepository,
        settings: AnalyticsSettings,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._review = review_repository
        self._settings = settings
        self._clock = clock or SystemClock()
        # Injected so tests get stable action ids and audit assertions stay exact.
        self._new_id = id_factory or (lambda: uuid.uuid4().hex)

    async def record_action(
        self,
        request: ReviewActionRequest,
        admin_id: str,
        context: QueryContext,
    ) -> ReviewActionResponse:
        """Record a review decision and apply its effect on the flag."""
        if request.admin_id is not None and request.admin_id != admin_id:
            raise AuthorizationError(
                "The admin_id in the request body does not match the authenticated caller.",
                details={"question_id": request.question_id},
            )

        existing = await self._call(
            self._review.get_flag(request.question_id, context),
            context,
            operation="get_flag",
        )

        if existing is not None and existing.status is FlagStatus.RETIRED:
            raise ReviewConflictError(
                "This question has been retired; no further review actions can be recorded.",
                details={
                    "question_id": request.question_id,
                    "flag_status": existing.status.value,
                },
            )

        now = self._clock.now()
        resulting_status = request.action.resulting_flag_status()

        action = ReviewActionRecord(
            action_id=self._new_id(),
            question_id=request.question_id,
            action=request.action,
            admin_id=admin_id,
            created_at=now,
            note=request.note,
            previous_flag_status=existing.status if existing else None,
            resulting_flag_status=(
                resulting_status
                if existing is not None
                else _status_for_new_record(request.action)
            ),
        )
        recorded = await self._call(
            self._review.record_action(action, context), context, operation="record_action"
        )

        flag = await self._apply_to_flag(
            existing=existing,
            action=request.action,
            admin_id=admin_id,
            now=now,
            question_id=request.question_id,
            context=context,
        )

        logger.info(
            "review action recorded",
            extra={
                "request_id": context.request_id,
                "question_id": request.question_id,
                "action": request.action.value,
                "admin_id_hash": _short_hash(admin_id),
                "previous_flag_status": existing.status.value if existing else None,
                "resulting_flag_status": flag.status.value if flag else None,
            },
        )
        return ReviewActionResponse(
            action=recorded,
            flag=flag_summary_from_record(flag),
            recorded_at=now,
        )

    async def get_history(
        self, question_id: str, context: QueryContext
    ) -> ReviewHistoryResponse:
        """Complete audit trail plus current flag state for one question."""
        actions, total = await self._call(
            self._review.list_actions(context, question_id=question_id, limit=1000, offset=0),
            context,
            operation="list_actions",
        )
        flag = await self._call(
            self._review.get_flag(question_id, context), context, operation="get_flag"
        )
        return ReviewHistoryResponse(
            question_id=question_id,
            current_flag=flag_summary_from_record(flag),
            actions=tuple(actions),
            total=total,
            calculated_at=self._clock.now(),
        )

    async def list_actions(
        self,
        context: QueryContext,
        *,
        question_id: str | None = None,
        admin_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ReviewAuditPage:
        """Paged audit log across questions."""
        actions, total = await self._call(
            self._review.list_actions(
                context,
                question_id=question_id,
                admin_id=admin_id,
                limit=limit,
                offset=offset,
            ),
            context,
            operation="list_actions",
        )
        return ReviewAuditPage(
            items=tuple(actions),
            total=total,
            limit=limit,
            offset=offset,
            calculated_at=self._clock.now(),
        )

    # ------------------------------------------------------------------ internal

    async def _apply_to_flag(
        self,
        *,
        existing: QuestionFlagRecord | None,
        action: ReviewActionType,
        admin_id: str,
        now,
        question_id: str,
        context: QueryContext,
    ) -> QuestionFlagRecord | None:
        """Transition the flag, or create the one record retirement requires."""
        if existing is not None:
            updated = existing.model_copy(
                update={
                    "status": action.resulting_flag_status(),
                    "resolved_at": now,
                    "resolved_by": admin_id,
                    "resolution_action": action,
                    "updated_at": now,
                }
            )
            return await self._call(
                self._review.upsert_flag(updated, context), context, operation="upsert_flag"
            )

        if action is ReviewActionType.QUESTION_RETIRED:
            # Retirement must outlive the request even with no prior flag,
            # otherwise the next evaluation would flag a withdrawn question.
            # Measurement fields stay null: nothing was measured here.
            record = QuestionFlagRecord(
                question_id=question_id,
                status=FlagStatus.RETIRED,
                reason=FlagReason.ADMINISTRATIVE_ACTION,
                flagged_at=now,
                flagged_by=admin_id,
                resolved_at=now,
                resolved_by=admin_id,
                resolution_action=action,
                updated_at=now,
            )
            return await self._call(
                self._review.upsert_flag(record, context), context, operation="upsert_flag"
            )

        # No Change / Question Updated on an unflagged question: the decision is
        # audited, but there is no flag state to carry forward.
        return None

    async def _call(self, awaitable, context: QueryContext, *, operation: str):
        try:
            return await run_with_deadline(awaitable, context)
        except AnalyticsError:
            raise
        except Exception as exc:
            logger.error(
                "review repository call failed",
                extra={"request_id": context.request_id, "operation": operation},
                exc_info=True,
            )
            raise RepositoryUnavailableError(
                f"{operation} failed: {exc}", details={"operation": operation}, cause=exc
            ) from exc


def _status_for_new_record(action: ReviewActionType) -> FlagStatus | None:
    """Flag status implied by an action against a question with no flag."""
    return FlagStatus.RETIRED if action is ReviewActionType.QUESTION_RETIRED else None


def _short_hash(value: str) -> str:
    """Stable non-reversible tag, so logs can correlate without storing identity."""
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
