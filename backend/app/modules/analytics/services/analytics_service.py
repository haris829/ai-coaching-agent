"""Analytics service - orchestrates reads, aggregation and presentation.

This is the only place that knows how to turn repository records into analytics
outputs, and *every* consumer goes through it: the JSON API, the CSV exports and
the flag evaluator. That is what guarantees an export can never disagree with a
dashboard (spec section 10).

The service is strictly read-only. It has no reference to a write interface, so
it is structurally incapable of modifying attempts, responses, scores or
pass/fail outcomes (spec section 17).

Cost profile
------------

* Zero-attempt detection costs one cheap ``count_attempts`` call; no scan is
  performed when nothing matches.
* Aggregation is a single streaming pass. Memory is bounded by the page size plus
  one accumulator per distinct question, never by attempt or response volume.
* A run that would exceed ``max_scanned_records`` raises rather than silently
  truncating, because half-scanned numbers are worse than an error.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any, TypeVar

from pydantic import ValidationError

from app.core.logging import get_logger
from app.core.time import Clock, SystemClock
from app.modules.analytics.cancellation import QueryContext, run_with_deadline
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.domain.analytics import (
    FlaggedQuestionsResponse,
    OverallAnalytics,
    PageMeta,
    QuestionAnalytics,
    QuestionAnalyticsPage,
    QuestionAnalyticsResponse,
)
from app.modules.analytics.domain.enums import (
    DataState,
    FlagStatus,
    QuestionSortField,
    SortDirection,
)
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.records import QuestionFlagRecord, QuestionMetadata
from app.modules.analytics.errors import (
    AnalyticsError,
    CalculationError,
    DatasetTooLargeError,
    NotFoundError,
    RepositoryUnavailableError,
    UpstreamDataInvalidError,
)
from app.modules.analytics.repositories.base import (
    AnalyticsRepository,
    stream_attempts,
    stream_responses,
)
from app.modules.analytics.services.aggregation import OverallAccumulator, QuestionAccumulator

__all__ = ["AnalyticsService"]

T = TypeVar("T")

logger = get_logger("analytics")


class AnalyticsService:
    """Computes dashboard, question and flag-candidate analytics."""

    def __init__(
        self,
        repository: AnalyticsRepository,
        settings: AnalyticsSettings,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._clock = clock or SystemClock()

    # ------------------------------------------------------------------- public

    async def get_overall_analytics(
        self, filters: AnalyticsFilters, context: QueryContext
    ) -> OverallAnalytics:
        """Average score, pass rate, completion rate and attempt volume."""
        settings = self._settings
        attempt_total = await self._guard(
            self._repository.count_attempts(filters, context),
            operation="count_attempts",
            context=context,
        )

        if attempt_total == 0:
            # Zero-attempt state: report it explicitly, compute nothing, and
            # never substitute zeros for absent data (spec section 12).
            logger.info(
                "overall analytics: no attempts matched",
                extra={"context": context.request_id, **filters.describe()},
            )
            return OverallAccumulator().build(
                filters=filters,
                calculated_at=self._clock.now(),
                decimal_places=settings.decimal_places,
            )

        self._assert_within_scan_limit(attempt_total, "attempts")

        accumulator = OverallAccumulator()
        try:
            async for attempt in stream_attempts(
                self._repository,
                filters,
                context,
                page_size=settings.repository_page_size,
            ):
                accumulator.add(attempt)
        except AnalyticsError:
            raise
        except Exception as exc:
            raise provider_failure(exc, operation="fetch_attempts_page") from exc

        result = self._build(
            lambda: accumulator.build(
                filters=filters,
                calculated_at=self._clock.now(),
                decimal_places=settings.decimal_places,
            )
        )
        logger.info(
            "overall analytics computed",
            extra={
                "request_id": context.request_id,
                "attempt_volume": result.attempt_volume,
                **filters.describe(),
            },
        )
        return result

    async def aggregate_question_analytics(
        self,
        filters: AnalyticsFilters,
        context: QueryContext,
        *,
        question_ids: Sequence[str] | None = None,
        include_ids_without_responses: Sequence[str] | None = None,
    ) -> tuple[QuestionAnalytics, ...]:
        """Question analytics for the filtered scope, ordered by question id.

        Shared entry point for the question endpoints, the flag evaluator and the
        CSV exporter, so all three see identical figures.

        ``include_ids_without_responses`` forces questions into the result even
        when they have no responses in scope. The content-review queue needs
        this: a flagged question must stay visible after its responses fall
        outside the current filters, otherwise the flag would appear to vanish.
        """
        accumulators = await self._aggregate(filters, context, question_ids=question_ids)

        for question_id in include_ids_without_responses or ():
            accumulators.setdefault(question_id, QuestionAccumulator(question_id=question_id))

        if not accumulators:
            return ()

        ids = sorted(accumulators)
        metadata = await self._guard(
            self._repository.fetch_question_metadata(ids, context),
            operation="fetch_question_metadata",
            context=context,
        )
        flags = await self._guard(
            self._repository.get_flags(ids, context), operation="get_flags", context=context
        )
        return self._build(lambda: self._materialise(accumulators, metadata, flags))

    async def list_question_analytics(
        self,
        filters: AnalyticsFilters,
        context: QueryContext,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: QuestionSortField = QuestionSortField.QUESTION_ID,
        direction: SortDirection = SortDirection.ASC,
        flagged_only: bool = False,
    ) -> QuestionAnalyticsPage:
        """Paged, sorted question analytics."""
        questions = await self.aggregate_question_analytics(filters, context)
        if flagged_only:
            questions = tuple(q for q in questions if q.is_flagged)

        ordered = sort_questions(questions, sort_by=sort_by, direction=direction)
        window = ordered[offset : offset + limit]
        return QuestionAnalyticsPage(
            items=window,
            page=PageMeta(
                limit=limit,
                offset=offset,
                returned=len(window),
                total=len(ordered),
                sort_by=sort_by,
                direction=direction,
            ),
            data_state=DataState.NO_ATTEMPTS if not questions else DataState.OK,
            filters=filters,
            calculated_at=self._clock.now(),
        )

    async def get_question_analytics(
        self, question_id: str, filters: AnalyticsFilters, context: QueryContext
    ) -> QuestionAnalyticsResponse:
        """Analytics for one question.

        A question with no responses in scope is reported with a
        ``NO_ATTEMPTS`` state rather than as a 404, provided the catalogue or the
        review store knows it exists. Only a genuinely unknown id is a 404.
        """
        questions = await self.aggregate_question_analytics(
            filters, context, question_ids=[question_id]
        )
        if questions:
            return QuestionAnalyticsResponse(
                question=questions[0],
                filters=filters,
                calculated_at=self._clock.now(),
            )

        metadata = await self._guard(
            self._repository.fetch_question_metadata([question_id], context),
            operation="fetch_question_metadata",
            context=context,
        )
        flag = await self._guard(
            self._repository.get_flag(question_id, context), operation="get_flag", context=context
        )
        if question_id not in metadata and flag is None:
            raise NotFoundError(
                "No question with this identifier is known to the assessment system.",
                details={"question_id": question_id},
            )

        empty = await self.aggregate_question_analytics(
            filters,
            context,
            question_ids=[question_id],
            include_ids_without_responses=[question_id],
        )
        return QuestionAnalyticsResponse(
            question=empty[0],
            filters=filters,
            calculated_at=self._clock.now(),
        )

    async def get_flagged_questions(
        self,
        filters: AnalyticsFilters,
        context: QueryContext,
        *,
        include_candidates: bool = False,
    ) -> FlaggedQuestionsResponse:
        """Content-review queue: persisted active flags, optionally with candidates.

        ``include_candidates`` adds questions that currently breach the threshold
        but have no persisted flag yet. This keeps analytics read-only: a caller
        can see what *would* be flagged without any write taking place. Persisting
        those candidates is an explicit, separate operation.
        """
        settings = self._settings
        active_flags = await self._guard(
            self._repository.get_flags(None, context, statuses=[FlagStatus.FLAGGED]),
            operation="get_flags",
            context=context,
        )
        flagged_ids = sorted(active_flags)

        if include_candidates:
            # Aggregate the whole scope so candidates can be identified, and pull
            # flagged questions in even when they have no responses in scope.
            questions = await self.aggregate_question_analytics(
                filters, context, include_ids_without_responses=flagged_ids
            )
            selected = tuple(q for q in questions if q.is_flagged or q.meets_flag_criteria)
        elif flagged_ids:
            questions = await self.aggregate_question_analytics(
                filters,
                context,
                question_ids=flagged_ids,
                include_ids_without_responses=flagged_ids,
            )
            selected = tuple(q for q in questions if q.is_flagged)
        else:
            selected = ()

        ordered = sort_questions(
            selected,
            sort_by=QuestionSortField.WRONG_ANSWER_RATE,
            direction=SortDirection.DESC,
        )
        return FlaggedQuestionsResponse(
            items=ordered,
            total=len(ordered),
            threshold_used=settings.flag_wrong_answer_rate_threshold,
            min_responses_required=settings.flag_min_responses,
            includes_unpersisted_candidates=include_candidates,
            filters=filters,
            calculated_at=self._clock.now(),
        )

    async def check_provider_health(self, context: QueryContext) -> bool:
        try:
            return await run_with_deadline(
                self._repository.health_check(context), context
            )
        except AnalyticsError:
            raise
        except Exception:
            return False

    @property
    def settings(self) -> AnalyticsSettings:
        return self._settings

    @property
    def clock(self) -> Clock:
        return self._clock

    # ------------------------------------------------------------------ internal

    async def _aggregate(
        self,
        filters: AnalyticsFilters,
        context: QueryContext,
        *,
        question_ids: Sequence[str] | None = None,
    ) -> dict[str, QuestionAccumulator]:
        """Single streaming pass over responses, one accumulator per question."""
        settings = self._settings
        accumulators: dict[str, QuestionAccumulator] = {}
        scanned = 0
        limit = settings.max_scanned_records

        try:
            async for response in stream_responses(
                self._repository,
                filters,
                context,
                page_size=settings.repository_page_size,
                question_ids=question_ids,
            ):
                scanned += 1
                if scanned > limit:
                    raise DatasetTooLargeError(
                        "The filtered dataset exceeds the maximum number of records this "
                        "service will scan. Narrow the date range, course or cohort filter.",
                        details={"max_scanned_records": limit},
                    )
                accumulator = accumulators.get(response.question_id)
                if accumulator is None:
                    accumulator = QuestionAccumulator(question_id=response.question_id)
                    accumulators[response.question_id] = accumulator
                accumulator.add(response)
        except AnalyticsError:
            raise
        except Exception as exc:
            raise provider_failure(exc, operation="fetch_responses_page") from exc

        logger.debug(
            "question aggregation complete",
            extra={
                "request_id": context.request_id,
                "responses_scanned": scanned,
                "questions": len(accumulators),
            },
        )
        return accumulators

    def _materialise(
        self,
        accumulators: Mapping[str, QuestionAccumulator],
        metadata: Mapping[str, QuestionMetadata],
        flags: Mapping[str, QuestionFlagRecord],
    ) -> tuple[QuestionAnalytics, ...]:
        settings = self._settings
        return tuple(
            accumulators[question_id].build(
                metadata=metadata.get(question_id),
                flag=flags.get(question_id),
                threshold=settings.flag_wrong_answer_rate_threshold,
                min_responses=settings.flag_min_responses,
                decimal_places=settings.decimal_places,
            )
            for question_id in sorted(accumulators)
        )

    def _assert_within_scan_limit(self, count: int, what: str) -> None:
        limit = self._settings.max_scanned_records
        if count > limit:
            raise DatasetTooLargeError(
                f"The filters match {count} {what}, above the configured scan limit of "
                f"{limit}. Narrow the date range, course or cohort filter.",
                details={"matched": count, "max_scanned_records": limit},
            )

    async def _guard(
        self, awaitable: Awaitable[T], *, operation: str, context: QueryContext
    ) -> T:
        """Run a repository call under the deadline, normalising its failures.

        Provider faults become :class:`RepositoryUnavailableError`, whose public
        message is generic: a driver error string can name hosts, users and
        schemas, none of which belongs in an API response.
        """
        try:
            return await run_with_deadline(awaitable, context)
        except AnalyticsError:
            raise
        except Exception as exc:
            logger.error(
                "repository call failed",
                extra={"request_id": context.request_id, "operation": operation},
                exc_info=True,
            )
            raise provider_failure(exc, operation=operation) from exc

    def _build(self, factory: Callable[[], T]) -> T:
        """Run a pure presentation step, converting faults to a typed error."""
        try:
            return factory()
        except AnalyticsError:
            raise
        except Exception as exc:
            raise CalculationError(f"analytics calculation failed: {exc}", cause=exc) from exc


def provider_failure(exc: Exception, *, operation: str) -> AnalyticsError:
    """Classify a raw provider exception as a typed domain error.

    A contract violation and an outage need different codes: one is permanent
    and actionable by the integrator, the other is transient and worth retrying.
    """
    if isinstance(exc, ValidationError):
        return UpstreamDataInvalidError(
            f"{operation} returned records violating the repository contract: {exc}",
            details={
                "operation": operation,
                "fields": sorted(
                    {".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()}
                ),
            },
            cause=exc,
        )
    return RepositoryUnavailableError(
        f"{operation} failed: {exc}", details={"operation": operation}, cause=exc
    )


def sort_questions(
    questions: Iterable[QuestionAnalytics],
    *,
    sort_by: QuestionSortField,
    direction: SortDirection,
) -> tuple[QuestionAnalytics, ...]:
    """Deterministic ordering with nulls last in both directions.

    A ``None`` metric means "no basis to compute", which is not a value that can
    be meaningfully ordered against real numbers. Sorting those rows to the end
    regardless of direction keeps the top of a report meaningful, and the
    question id tie-break makes the order reproducible.
    """
    items = list(questions)
    reverse = direction is SortDirection.DESC

    def value_of(question: QuestionAnalytics) -> Any:
        if sort_by is QuestionSortField.QUESTION_ID:
            return question.question_id
        return getattr(question, sort_by.value)

    with_value = [q for q in items if value_of(q) is not None]
    without_value = [q for q in items if value_of(q) is None]

    with_value.sort(key=lambda q: q.question_id)
    with_value.sort(key=value_of, reverse=reverse)
    without_value.sort(key=lambda q: q.question_id)
    return tuple(with_value + without_value)
