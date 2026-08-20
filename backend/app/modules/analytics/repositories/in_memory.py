"""In-memory reference implementation of the repository contracts.

**This is not a database and not a storage layer.** It is an in-process
reference provider with three jobs:

1. document, in executable form, exactly what an integrator's implementation
   has to do (filtering, stable pagination, cursor handling),
2. let the API be exercised end to end before the real provider exists,
3. back the test suite.

Nothing is persisted: data is handed to the constructor by the caller and lives
only for the lifetime of the object. Replace it at the dependency-injection
seam with an implementation over the real assessment system - no service or
route changes are required.

The assessment side is genuinely read-only: :class:`InMemoryAnalyticsRepository`
stores its records in immutable tuples and offers no mutator. The review store
is separate and shared, mirroring production, where analytics reads flags that
the review service writes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence

from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.enums import FlagStatus
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.records import (
    AttemptRecord,
    Page,
    PageRequest,
    QuestionFlagRecord,
    QuestionMetadata,
    ResponseRecord,
    ReviewActionRecord,
)
from app.modules.analytics.errors import ReviewConflictError
from app.modules.analytics.repositories.base import AnalyticsRepository, ReviewRepository

__all__ = [
    "InMemoryReviewStore",
    "InMemoryAnalyticsRepository",
    "InMemoryReviewRepository",
]


class InMemoryReviewStore:
    """Shared review state: flags plus the append-only action log."""

    def __init__(
        self,
        flags: Iterable[QuestionFlagRecord] = (),
        actions: Iterable[ReviewActionRecord] = (),
    ) -> None:
        self._flags: dict[str, QuestionFlagRecord] = {f.question_id: f for f in flags}
        self._actions: list[ReviewActionRecord] = list(actions)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------- reads

    def flags_snapshot(self) -> Mapping[str, QuestionFlagRecord]:
        return dict(self._flags)

    def actions_snapshot(self) -> tuple[ReviewActionRecord, ...]:
        return tuple(self._actions)

    def select_flags(
        self,
        question_ids: Sequence[str] | None,
        statuses: Sequence[FlagStatus] | None,
    ) -> dict[str, QuestionFlagRecord]:
        if question_ids is None:
            candidates = self._flags.values()
        else:
            wanted = set(question_ids)
            candidates = [f for qid, f in self._flags.items() if qid in wanted]
        if statuses is not None:
            allowed = set(statuses)
            candidates = [f for f in candidates if f.status in allowed]
        return {f.question_id: f for f in candidates}

    # ------------------------------------------------------------------ writes

    async def put_flag(self, flag: QuestionFlagRecord) -> QuestionFlagRecord:
        async with self._lock:
            self._flags[flag.question_id] = flag
            return flag

    async def append_action(self, action: ReviewActionRecord) -> ReviewActionRecord:
        async with self._lock:
            if any(existing.action_id == action.action_id for existing in self._actions):
                raise ReviewConflictError(
                    "A review action with this identifier has already been recorded.",
                    details={"action_id": action.action_id},
                )
            self._actions.append(action)
            return action


class InMemoryAnalyticsRepository(AnalyticsRepository):
    """Read-only reference provider over caller-supplied assessment records."""

    def __init__(
        self,
        attempts: Iterable[AttemptRecord] = (),
        responses: Iterable[ResponseRecord] = (),
        questions: Iterable[QuestionMetadata] = (),
        review_store: InMemoryReviewStore | None = None,
    ) -> None:
        # Sorted once, so pagination order is stable across calls - the same
        # guarantee a real provider gets from ordering by primary key.
        self._attempts: tuple[AttemptRecord, ...] = tuple(
            sorted(attempts, key=lambda a: (a.started_at, a.attempt_id))
        )
        self._responses: tuple[ResponseRecord, ...] = tuple(
            sorted(responses, key=lambda r: r.response_id)
        )
        self._questions: dict[str, QuestionMetadata] = {q.question_id: q for q in questions}
        self._attempt_index: dict[str, AttemptRecord] = {a.attempt_id: a for a in self._attempts}
        self._review_store = review_store or InMemoryReviewStore()
        #: Call counter, used by tests to prove filtering is pushed to the provider.
        self.call_log: list[str] = []

    # ------------------------------------------------------------- diagnostics

    @property
    def review_store(self) -> InMemoryReviewStore:
        return self._review_store

    def snapshot_fingerprint(self) -> str:
        """Stable hash of all assessment data.

        Integrity tests compare this before and after analytics runs to prove
        UC-10 never mutates source data.
        """
        payload = {
            "attempts": [a.model_dump(mode="json") for a in self._attempts],
            "responses": [r.model_dump(mode="json") for r in self._responses],
            "questions": [
                self._questions[k].model_dump(mode="json") for k in sorted(self._questions)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    # ----------------------------------------------------------------- queries

    async def count_attempts(self, filters: AnalyticsFilters, context: QueryContext) -> int:
        context.raise_if_stopped()
        self.call_log.append("count_attempts")
        return sum(1 for a in self._attempts if filters.matches_attempt(a))

    async def fetch_attempts_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        context: QueryContext,
    ) -> Page[AttemptRecord]:
        context.raise_if_stopped()
        self.call_log.append("fetch_attempts_page")
        matching = [a for a in self._attempts if filters.matches_attempt(a)]
        return _slice(matching, page, total=len(matching))

    async def fetch_responses_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        context: QueryContext,
        *,
        question_ids: Sequence[str] | None = None,
    ) -> Page[ResponseRecord]:
        context.raise_if_stopped()
        self.call_log.append("fetch_responses_page")
        wanted_questions = set(question_ids) if question_ids is not None else None

        matching: list[ResponseRecord] = []
        for response in self._responses:
            if wanted_questions is not None and response.question_id not in wanted_questions:
                continue
            attempt = self._attempt_index.get(response.attempt_id)
            # An orphan response (no parent attempt) cannot be attributed to a
            # course, cohort or date, so it is out of scope by definition.
            if attempt is None or not filters.matches_attempt(attempt):
                continue
            matching.append(response)
        return _slice(matching, page, total=len(matching))

    async def fetch_question_metadata(
        self,
        question_ids: Sequence[str],
        context: QueryContext,
    ) -> Mapping[str, QuestionMetadata]:
        context.raise_if_stopped()
        self.call_log.append("fetch_question_metadata")
        return {qid: self._questions[qid] for qid in question_ids if qid in self._questions}

    async def get_flags(
        self,
        question_ids: Sequence[str] | None,
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        context.raise_if_stopped()
        self.call_log.append("get_flags")
        return self._review_store.select_flags(question_ids, statuses)

    async def health_check(self, context: QueryContext) -> bool:
        return True


class InMemoryReviewRepository(ReviewRepository):
    """Reference implementation of the review write surface."""

    def __init__(self, review_store: InMemoryReviewStore | None = None) -> None:
        self._store = review_store or InMemoryReviewStore()

    @property
    def store(self) -> InMemoryReviewStore:
        return self._store

    async def get_flags(
        self,
        question_ids: Sequence[str] | None,
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        context.raise_if_stopped()
        return self._store.select_flags(question_ids, statuses)

    async def upsert_flag(
        self, flag: QuestionFlagRecord, context: QueryContext
    ) -> QuestionFlagRecord:
        context.raise_if_stopped()
        return await self._store.put_flag(flag)

    async def record_action(
        self, action: ReviewActionRecord, context: QueryContext
    ) -> ReviewActionRecord:
        context.raise_if_stopped()
        return await self._store.append_action(action)

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
        actions = [
            a
            for a in self._store.actions_snapshot()
            if (question_id is None or a.question_id == question_id)
            and (admin_id is None or a.admin_id == admin_id)
        ]
        # Newest first, with action_id as a deterministic tie-break.
        actions.sort(key=lambda a: (a.created_at, a.action_id), reverse=True)
        total = len(actions)
        return tuple(actions[offset : offset + limit]), total


def _slice(records: Sequence[object], page: PageRequest, *, total: int) -> Page:
    """Apply cursor pagination to an ordered list.

    The cursor is an opaque offset token. A real provider would use a keyset
    cursor; the contract only requires that it be opaque and stable.
    """
    start = _decode_cursor(page.cursor)
    window = records[start : start + page.limit]
    end = start + len(window)
    next_cursor = str(end) if end < len(records) else None
    return Page(items=tuple(window), next_cursor=next_cursor, total=total)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError):
        raise ValueError(f"malformed pagination cursor: {cursor!r}") from None
    if value < 0:
        raise ValueError(f"malformed pagination cursor: {cursor!r}")
    return value

