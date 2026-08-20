"""Content-review flag evaluation (spec sections 8 and 18).

A question is flagged when its wrong-answer rate exceeds the configured
threshold *and* it has enough graded responses for that rate to mean anything.
Both values come from :class:`~app.modules.analytics.config.AnalyticsSettings`; there
is no literal threshold anywhere in this module.

Flag lifecycle, and why it is shaped this way
---------------------------------------------

``no flag -> FLAGGED``
    Threshold breached with sufficient data.

``FLAGGED -> FLAGGED`` (untouched)
    Re-running evaluation never re-raises, re-times or resets an active flag.
    The original measurement is the evidence a reviewer will look at.

``FLAGGED -> stays FLAGGED even when the rate drops``
    Recalculation must not erase a flag. If later cohorts answer the question
    correctly and the rate falls below the threshold, the flag remains until an
    administrator explicitly decides what to do about it. Analytics does not get
    to close a review.

``FLAGGED -> RESOLVED / RETIRED``
    Only through a review action - see
    :mod:`app.modules.analytics.services.review_service`.

``RESOLVED -> FLAGGED``
    Only on *fresh evidence*: graded responses recorded after the resolution
    timestamp, at least ``reflag_min_new_responses`` of them. Without that rule a
    resolved flag would either spring straight back on the same data that raised
    it, or never come back no matter how badly the question performed later.

``RETIRED``
    Terminal. A withdrawn question is never flagged again.

Write scope: this service writes flags to the review store and nothing else.
Assessment data is read through the read-only analytics repository.
"""

from __future__ import annotations

from datetime import datetime

from app.core.logging import get_logger
from app.core.time import Clock, SystemClock
from app.modules.analytics.cancellation import QueryContext, run_with_deadline
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.domain.analytics import FlagEvaluationResult, QuestionAnalytics
from app.modules.analytics.domain.enums import FlagReason, FlagStatus
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.records import QuestionFlagRecord
from app.modules.analytics.errors import AnalyticsError, RepositoryUnavailableError
from app.modules.analytics.repositories.base import (
    AnalyticsRepository,
    ReviewRepository,
    stream_responses,
)
from app.modules.analytics.services.analytics_service import AnalyticsService

__all__ = ["FlagService"]

logger = get_logger("flags")


class FlagService:
    """Evaluates threshold breaches and persists the resulting flags."""

    def __init__(
        self,
        analytics_service: AnalyticsService,
        analytics_repository: AnalyticsRepository,
        review_repository: ReviewRepository,
        settings: AnalyticsSettings,
        clock: Clock | None = None,
    ) -> None:
        self._analytics = analytics_service
        self._repository = analytics_repository
        self._review = review_repository
        self._settings = settings
        self._clock = clock or SystemClock()

    async def evaluate(
        self,
        filters: AnalyticsFilters,
        context: QueryContext,
        *,
        triggered_by: str = "system",
    ) -> FlagEvaluationResult:
        """Evaluate every question in scope and persist new flags.

        Returns a full account of what happened to each question, including the
        ones deliberately left alone, so an operator can see that a dropped rate
        did not silently clear anything.
        """
        settings = self._settings
        questions = await self._analytics.aggregate_question_analytics(filters, context)

        newly_flagged: list[str] = []
        re_flagged: list[str] = []
        already_flagged: list[str] = []
        retained: list[str] = []
        insufficient: list[str] = []
        retired: list[str] = []

        for question in questions:
            await context.acheck()
            flag = question.flag

            if flag is not None and flag.status is FlagStatus.RETIRED:
                retired.append(question.question_id)
                continue

            if flag is not None and flag.status is FlagStatus.FLAGGED:
                # Active flags are immutable until reviewed, whether or not the
                # current rate still breaches the threshold.
                if question.meets_flag_criteria:
                    already_flagged.append(question.question_id)
                else:
                    retained.append(question.question_id)
                continue

            if not question.meets_flag_criteria:
                if question.graded_count < settings.flag_min_responses:
                    insufficient.append(question.question_id)
                continue

            if flag is not None and flag.status is FlagStatus.RESOLVED:
                if not await self._has_fresh_evidence(question, flag.resolved_at, filters, context):
                    continue
                await self._persist_flag(question, context, triggered_by=triggered_by)
                re_flagged.append(question.question_id)
                continue

            await self._persist_flag(question, context, triggered_by=triggered_by)
            newly_flagged.append(question.question_id)

        result = FlagEvaluationResult(
            evaluated_questions=len(questions),
            newly_flagged=tuple(newly_flagged),
            re_flagged=tuple(re_flagged),
            already_flagged=tuple(already_flagged),
            below_threshold_retained=tuple(retained),
            skipped_insufficient_data=tuple(insufficient),
            skipped_retired=tuple(retired),
            threshold_used=settings.flag_wrong_answer_rate_threshold,
            min_responses_required=settings.flag_min_responses,
            filters=filters,
            calculated_at=self._clock.now(),
        )
        logger.info(
            "flag evaluation complete",
            extra={
                "request_id": context.request_id,
                "evaluated": result.evaluated_questions,
                "newly_flagged": len(result.newly_flagged),
                "re_flagged": len(result.re_flagged),
                "retained": len(result.below_threshold_retained),
                "threshold": result.threshold_used,
            },
        )
        return result

    # ------------------------------------------------------------------ internal

    async def _persist_flag(
        self,
        question: QuestionAnalytics,
        context: QueryContext,
        *,
        triggered_by: str,
    ) -> QuestionFlagRecord:
        now = self._clock.now()
        record = QuestionFlagRecord(
            question_id=question.question_id,
            status=FlagStatus.FLAGGED,
            reason=FlagReason.WRONG_ANSWER_RATE_EXCEEDED,
            wrong_answer_rate=question.wrong_answer_rate,
            threshold_used=question.flag_threshold,
            graded_responses_at_flag=question.graded_count,
            flagged_at=now,
            flagged_by=triggered_by,
            # A re-raised flag starts a clean review cycle: the previous
            # resolution stays in the audit log, not on the live record.
            resolved_at=None,
            resolved_by=None,
            resolution_action=None,
            updated_at=now,
        )
        try:
            return await run_with_deadline(self._review.upsert_flag(record, context), context)
        except AnalyticsError:
            raise
        except Exception as exc:
            raise RepositoryUnavailableError(
                f"flag persistence failed: {exc}",
                details={"operation": "upsert_flag"},
                cause=exc,
            ) from exc

    async def _has_fresh_evidence(
        self,
        question: QuestionAnalytics,
        resolved_at: datetime | None,
        filters: AnalyticsFilters,
        context: QueryContext,
    ) -> bool:
        """Whether enough graded responses arrived after the flag was resolved.

        Scans only this one question, pushed down to the provider, so the check
        costs a narrow query rather than another full pass.
        """
        settings = self._settings
        if not settings.reflag_enabled or resolved_at is None:
            return False

        fresh = 0
        try:
            async for response in stream_responses(
                self._repository,
                filters,
                context,
                page_size=settings.repository_page_size,
                question_ids=[question.question_id],
            ):
                if response.is_correct is None or response.answered_at is None:
                    # Undated or ungraded responses cannot be proven to be new,
                    # so they never count towards re-flagging.
                    continue
                if response.answered_at > resolved_at:
                    fresh += 1
                    if fresh >= settings.reflag_min_new_responses:
                        return True
        except AnalyticsError:
            raise
        except Exception as exc:
            raise RepositoryUnavailableError(
                f"fresh-evidence scan failed: {exc}",
                details={"operation": "fetch_responses_page"},
                cause=exc,
            ) from exc
        return False

    @property
    def settings(self) -> AnalyticsSettings:
        return self._settings
