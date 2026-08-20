"""Test doubles for the repository contracts.

These exist to exercise the failure and integrity paths that a healthy provider
never produces: outages, hangs, contract violations, and any attempt to mutate
assessment data.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.enums import FlagStatus
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.records import (
    Page,
    PageRequest,
    QuestionFlagRecord,
    QuestionMetadata,
    ReviewActionRecord,
)
from app.modules.analytics.repositories.base import AnalyticsRepository, ReviewRepository


class FailingAnalyticsRepository(AnalyticsRepository):
    """Every read raises the provider error given at construction."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or ConnectionResetError(
            "psycopg2.OperationalError: could not connect to host db-prod-01 as user analytics"
        )

    async def count_attempts(self, filters: AnalyticsFilters, context: QueryContext) -> int:
        raise self.error

    async def fetch_attempts_page(
        self, filters: AnalyticsFilters, page: PageRequest, context: QueryContext
    ) -> Page:
        raise self.error

    async def fetch_responses_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        context: QueryContext,
        *,
        question_ids: Sequence[str] | None = None,
    ) -> Page:
        raise self.error

    async def fetch_question_metadata(
        self, question_ids: Sequence[str], context: QueryContext
    ) -> Mapping[str, QuestionMetadata]:
        raise self.error

    async def get_flags(
        self,
        question_ids: Sequence[str] | None,
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        raise self.error

    async def health_check(self, context: QueryContext) -> bool:
        raise self.error


class HangingAnalyticsRepository(AnalyticsRepository):
    """Never returns, and never consults the query context.

    Proves the service enforces the deadline itself rather than relying on a
    cooperative provider.
    """

    def __init__(self, delay: float = 60.0) -> None:
        self.delay = delay

    async def count_attempts(self, filters: AnalyticsFilters, context: QueryContext) -> int:
        await asyncio.sleep(self.delay)
        return 0  # pragma: no cover

    async def fetch_attempts_page(
        self, filters: AnalyticsFilters, page: PageRequest, context: QueryContext
    ) -> Page:
        await asyncio.sleep(self.delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def fetch_responses_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        context: QueryContext,
        *,
        question_ids: Sequence[str] | None = None,
    ) -> Page:
        await asyncio.sleep(self.delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def fetch_question_metadata(
        self, question_ids: Sequence[str], context: QueryContext
    ) -> Mapping[str, QuestionMetadata]:
        await asyncio.sleep(self.delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def get_flags(
        self,
        question_ids: Sequence[str] | None,
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        await asyncio.sleep(self.delay)
        raise AssertionError("unreachable")  # pragma: no cover


class ContractViolatingRepository(AnalyticsRepository):
    """Returns a record that breaks the repository contract.

    Models the realistic integration fault of a provider sending a score on a
    different scale.
    """

    async def count_attempts(self, filters: AnalyticsFilters, context: QueryContext) -> int:
        return 1

    async def fetch_attempts_page(
        self, filters: AnalyticsFilters, page: PageRequest, context: QueryContext
    ) -> Page:
        from app.modules.analytics.domain.records import AttemptRecord

        return Page(
            items=(
                AttemptRecord(
                    attempt_id="a1",
                    course_id="course-1",
                    learner_id="learner-1",
                    assessment_type="STANDARD_QUIZ",
                    status="COMPLETED",
                    started_at="2026-01-05T09:00:00Z",
                    score=880.0,  # 0-1000 scale: violates the 0-100 contract
                ),
            ),
        )

    async def fetch_responses_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        context: QueryContext,
        *,
        question_ids: Sequence[str] | None = None,
    ) -> Page:
        return Page()

    async def fetch_question_metadata(
        self, question_ids: Sequence[str], context: QueryContext
    ) -> Mapping[str, QuestionMetadata]:
        return {}

    async def get_flags(
        self,
        question_ids: Sequence[str] | None,
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        return {}


class RecordingRepository:
    """Proxy that records every repository method a caller touches.

    Used by the read-only integrity tests: the recorded call list must never
    contain a mutating method.
    """

    MUTATING_PREFIXES = ("save", "create", "update", "delete", "insert", "upsert", "write", "set_")

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute) or name.startswith("_"):
            return attribute

        async def recording(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return await attribute(*args, **kwargs)

        def recording_sync(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return attribute(*args, **kwargs)

        return recording if asyncio.iscoroutinefunction(attribute) else recording_sync

    @property
    def mutating_calls(self) -> list[str]:
        return [
            call
            for call in self.calls
            if any(call.startswith(prefix) for prefix in self.MUTATING_PREFIXES)
        ]


class FailingReviewRepository(ReviewRepository):
    """Review store whose writes fail."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or TimeoutError("review store write timed out after 5000ms")

    async def get_flags(
        self,
        question_ids: Sequence[str] | None,
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        return {}

    async def upsert_flag(
        self, flag: QuestionFlagRecord, context: QueryContext
    ) -> QuestionFlagRecord:
        raise self.error

    async def record_action(
        self, action: ReviewActionRecord, context: QueryContext
    ) -> ReviewActionRecord:
        raise self.error

    async def list_actions(
        self,
        context: QueryContext,
        *,
        question_id: str | None = None,
        admin_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Sequence[ReviewActionRecord], int]:
        raise self.error
