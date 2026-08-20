"""Repository interfaces - UC-10's only route to data (spec sections 21, 22).

UC-10 owns no storage. The integrating system supplies implementations of these
interfaces on top of whatever it already has: SQL, a service call, an event
store, a warehouse.

Two interfaces, split by write capability:

:class:`AnalyticsRepository`
    **Strictly read-only.** It exposes no method that could change an attempt,
    a response, a score, a pass/fail outcome or learner data. That is a
    structural guarantee, not a convention: the read-only integrity requirement
    (spec section 17) is enforced by the shape of this interface, so no service
    is in a position to violate it.

:class:`ReviewRepository`
    The single write surface, scoped to the review store: content-review flags
    and the review-action audit log. Assessment data is not reachable from it.

Both extend :class:`FlagReader`, so a single class can implement both and an
integrator can back them with one connection pool.

Implementation requirements
---------------------------

* **Filtering happens in the provider.** Every ``filters`` argument must be
  translated into the provider's own query. UC-10 pages through the results and
  never post-filters, which is what keeps aggregation viable well past the
  500-attempt baseline.
* **Filter semantics are fixed** by
  :meth:`~app.modules.analytics.domain.filters.AnalyticsFilters.matches_attempt`;
  reproduce them exactly, including the half-open date range.
* **Responses follow their attempt.** ``fetch_responses_page`` must return
  responses whose *parent attempt* satisfies the filters.
* **Pagination must be stable.** Order by an immutable key (e.g. primary key) so
  paging cannot skip or repeat a record.
* **Honour the context.** Pass ``context.remaining_seconds()`` to the driver as a
  statement timeout and call ``context.raise_if_stopped()`` between batches.
* **Fail with typed errors.** Wrap driver failures in
  :class:`~app.modules.analytics.errors.RepositoryUnavailableError`; the service layer
  converts anything else, but a typed error keeps the log accurate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence

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

__all__ = [
    "FlagReader",
    "AnalyticsRepository",
    "ReviewRepository",
    "stream_attempts",
    "stream_responses",
    "assert_read_only",
]


class FlagReader(ABC):
    """Read access to persisted content-review flags."""

    @abstractmethod
    async def get_flags(
        self,
        question_ids: Sequence[str] | None,
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        """Return flags keyed by question id.

        ``question_ids=None`` means "every flagged question", which is how the
        content-review queue is populated without first enumerating the
        catalogue. Questions without a flag are simply absent from the mapping.
        """

    async def get_flag(
        self, question_id: str, context: QueryContext
    ) -> QuestionFlagRecord | None:
        """Return one flag, or ``None``.

        Default implementation delegates to :meth:`get_flags`; override when the
        provider can answer a single-key lookup more cheaply.
        """
        flags = await self.get_flags([question_id], context)
        return flags.get(question_id)


class AnalyticsRepository(FlagReader, ABC):
    """Read-only access to assessment data held by the external system.

    Deliberately contains no create, update or delete operation of any kind.
    """

    @abstractmethod
    async def count_attempts(self, filters: AnalyticsFilters, context: QueryContext) -> int:
        """Number of attempts matching ``filters``.

        Used to detect the zero-attempt state before doing any aggregation work,
        so an empty result costs one cheap query rather than a full scan.
        """

    @abstractmethod
    async def fetch_attempts_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        context: QueryContext,
    ) -> Page[AttemptRecord]:
        """One page of attempts matching ``filters``, in a stable order."""

    @abstractmethod
    async def fetch_responses_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        context: QueryContext,
        *,
        question_ids: Sequence[str] | None = None,
    ) -> Page[ResponseRecord]:
        """One page of question responses whose parent attempt matches ``filters``.

        ``question_ids`` narrows the query to specific questions and must be
        applied by the provider, so single-question analytics does not scan the
        entire response set.
        """

    @abstractmethod
    async def fetch_question_metadata(
        self,
        question_ids: Sequence[str],
        context: QueryContext,
    ) -> Mapping[str, QuestionMetadata]:
        """Metadata for the given questions, keyed by question id.

        Missing ids are omitted rather than raising: a question can be deleted
        from the catalogue while its historical responses remain, and analytics
        must still report on those responses.
        """

    async def health_check(self, context: QueryContext) -> bool:
        """Whether the provider is reachable. Override with a real probe."""
        return True


class ReviewRepository(FlagReader, ABC):
    """Write surface for the review store: flags and the audit log."""

    @abstractmethod
    async def upsert_flag(
        self, flag: QuestionFlagRecord, context: QueryContext
    ) -> QuestionFlagRecord:
        """Create or replace the flag record for a question.

        Must be idempotent for an unchanged record.
        """

    @abstractmethod
    async def record_action(
        self, action: ReviewActionRecord, context: QueryContext
    ) -> ReviewActionRecord:
        """Append an immutable review-action audit entry.

        Implementations must never update or delete an existing entry;
        ``action_id`` collisions are a caller bug and should raise.
        """

    @abstractmethod
    async def list_actions(
        self,
        context: QueryContext,
        *,
        question_id: str | None = None,
        admin_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Sequence[ReviewActionRecord], int]:
        """Audit entries plus the total count, newest first."""


async def stream_attempts(
    repository: AnalyticsRepository,
    filters: AnalyticsFilters,
    context: QueryContext,
    *,
    page_size: int,
    max_records: int | None = None,
) -> AsyncIterator[AttemptRecord]:
    """Yield attempts page by page, enforcing deadline and scan limits.

    Memory stays bounded by ``page_size`` regardless of dataset size: one page is
    held at a time and released before the next is fetched.
    """
    from app.modules.analytics.cancellation import run_with_deadline  # local import avoids a cycle

    cursor: str | None = None
    scanned = 0
    while True:
        await context.acheck()
        page = await run_with_deadline(
            repository.fetch_attempts_page(
                filters, PageRequest(cursor=cursor, limit=page_size), context
            ),
            context,
        )
        for record in page.items:
            yield record
        scanned += len(page.items)
        if max_records is not None and scanned >= max_records:
            return
        if not page.has_more or not page.items:
            return
        cursor = page.next_cursor


async def stream_responses(
    repository: AnalyticsRepository,
    filters: AnalyticsFilters,
    context: QueryContext,
    *,
    page_size: int,
    question_ids: Sequence[str] | None = None,
    max_records: int | None = None,
) -> AsyncIterator[ResponseRecord]:
    """Yield responses page by page, enforcing deadline and scan limits."""
    from app.modules.analytics.cancellation import run_with_deadline

    ids: Sequence[str] | None = tuple(question_ids) if question_ids is not None else None
    cursor: str | None = None
    scanned = 0
    while True:
        await context.acheck()
        page = await run_with_deadline(
            repository.fetch_responses_page(
                filters,
                PageRequest(cursor=cursor, limit=page_size),
                context,
                question_ids=ids,
            ),
            context,
        )
        for record in page.items:
            yield record
        scanned += len(page.items)
        if max_records is not None and scanned >= max_records:
            return
        if not page.has_more or not page.items:
            return
        cursor = page.next_cursor


def assert_read_only(repository_cls: type) -> None:
    """Guard used by tests: an analytics repository must expose no writes.

    Raises ``AssertionError`` when a subclass introduces a mutating method name,
    which is what keeps a future integrator from quietly widening the read-only
    boundary.
    """
    forbidden_prefixes = ("save", "create", "update", "delete", "insert", "upsert", "write", "set_")
    offenders: list[str] = []
    for name in dir(repository_cls):
        if name.startswith("_"):
            continue
        if any(name.startswith(prefix) for prefix in forbidden_prefixes):
            offenders.append(name)
    if offenders:
        raise AssertionError(
            f"{repository_cls.__name__} exposes mutating methods on a read-only "
            f"interface: {sorted(offenders)}"
        )

