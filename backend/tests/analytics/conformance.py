"""Conformance checks for repository implementations.

Shipped as part of the package so the team that writes the real
:class:`~app.modules.analytics.repositories.base.AnalyticsRepository` can prove it
behaves the way the services assume, against their own data, before any
integration testing::

    from tests.analytics.conformance import verify_analytics_repository

    async def test_our_repository_conforms(pool):
        report = await verify_analytics_repository(
            MyAssessmentRepository(pool),
            AnalyticsFilters(course_id="course-1"),
            QueryContext.create(timeout_seconds=30),
        )
        assert report.passed, report.failures

Everything here is read-only: the checks observe what the provider returns and
never write. They catch the mistakes that quietly corrupt analytics rather than
crash it - unstable pagination, filters applied in UC-10's process instead of the
provider's, responses returned for out-of-scope attempts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.records import PageRequest
from app.modules.analytics.repositories.base import AnalyticsRepository

__all__ = ["ConformanceReport", "verify_analytics_repository"]


@dataclass
class ConformanceReport:
    """Outcome of a conformance run."""

    checks_run: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    attempts_seen: int = 0
    responses_seen: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures

    def _record(self, name: str, condition: bool, message: str) -> None:
        self.checks_run.append(name)
        if not condition:
            self.failures.append(f"{name}: {message}")

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        return (
            f"{status} - {len(self.checks_run)} checks, {len(self.failures)} failures, "
            f"{self.attempts_seen} attempts and {self.responses_seen} responses inspected"
        )


async def verify_analytics_repository(
    repository: AnalyticsRepository,
    filters: AnalyticsFilters,
    context: QueryContext,
    *,
    page_size: int = 10,
) -> ConformanceReport:
    """Check a repository implementation against the contract.

    ``filters`` should select a modest, non-empty slice of real data. Pass a
    small ``page_size`` so pagination is genuinely exercised.
    """
    report = ConformanceReport()

    # --- count agrees with what paging actually yields -----------------------
    counted = await repository.count_attempts(filters, context)
    attempts, attempt_ids = await _drain_attempts(repository, filters, context, page_size)
    report.attempts_seen = len(attempts)
    report._record(
        "count_matches_pagination",
        counted == len(attempts),
        f"count_attempts reported {counted} but paging yielded {len(attempts)}",
    )

    # --- pagination is stable: no duplicates, no omissions -------------------
    report._record(
        "pagination_yields_distinct_records",
        len(attempt_ids) == len(set(attempt_ids)),
        "the same attempt was returned on more than one page; order by an immutable key",
    )
    second_pass, _ = await _drain_attempts(repository, filters, context, page_size)
    report._record(
        "pagination_is_repeatable",
        [a.attempt_id for a in attempts] == [a.attempt_id for a in second_pass],
        "two identical queries returned attempts in different orders",
    )

    # --- filters are honoured by the provider -------------------------------
    violations = [a.attempt_id for a in attempts if not filters.matches_attempt(a)]
    report._record(
        "filters_applied_by_provider",
        not violations,
        f"returned attempts outside the filter scope: {violations[:5]}",
    )

    # --- a smaller page size must not change the population -----------------
    if len(attempts) > 1:
        single, _ = await _drain_attempts(repository, filters, context, 1)
        report._record(
            "page_size_does_not_change_results",
            {a.attempt_id for a in single} == set(attempt_ids),
            "changing the page size changed which attempts were returned",
        )

    # --- responses belong to in-scope attempts ------------------------------
    responses = await _drain_responses(repository, filters, context, page_size)
    report.responses_seen = len(responses)
    in_scope = set(attempt_ids)
    orphans = [r.response_id for r in responses if r.attempt_id not in in_scope]
    report._record(
        "responses_follow_their_attempt",
        not orphans,
        f"returned responses whose attempt is out of scope: {orphans[:5]}",
    )
    response_ids = [r.response_id for r in responses]
    report._record(
        "response_pagination_yields_distinct_records",
        len(response_ids) == len(set(response_ids)),
        "the same response was returned on more than one page",
    )

    # --- question narrowing is applied by the provider ----------------------
    question_ids = sorted({r.question_id for r in responses})[:1]
    if question_ids:
        narrowed = await _drain_responses(
            repository, filters, context, page_size, question_ids=question_ids
        )
        wrong_question = [r.response_id for r in narrowed if r.question_id not in question_ids]
        report._record(
            "question_narrowing_applied",
            not wrong_question,
            "question_ids was ignored; narrowing must happen in the provider",
        )
        expected = [r.response_id for r in responses if r.question_id in question_ids]
        report._record(
            "question_narrowing_is_complete",
            set(narrowed and [r.response_id for r in narrowed]) == set(expected),
            "narrowing by question_ids dropped or added responses",
        )

        # --- metadata lookups are keyed correctly --------------------------
        metadata = await repository.fetch_question_metadata(question_ids, context)
        mismatched = [key for key, value in metadata.items() if value.question_id != key]
        report._record(
            "metadata_is_keyed_by_question_id",
            not mismatched,
            f"metadata mapping keys disagree with their records: {mismatched[:5]}",
        )
        report._record(
            "metadata_omits_unknown_ids_without_raising",
            isinstance(
                await repository.fetch_question_metadata(
                    ["uc10-conformance-unknown-id"], context
                ),
                dict | type(metadata),
            ),
            "an unknown question id must be omitted, not raise",
        )

    # --- flags are readable and keyed correctly -----------------------------
    flags = await repository.get_flags(None, context)
    misfiled = [key for key, value in flags.items() if value.question_id != key]
    report._record(
        "flags_are_keyed_by_question_id",
        not misfiled,
        f"flag mapping keys disagree with their records: {misfiled[:5]}",
    )

    # --- empty scope is cheap and honest ------------------------------------
    empty_filters = filters.model_copy(
        update={"course_id": "uc10-conformance-course-that-cannot-exist"}
    )
    report._record(
        "empty_scope_counts_zero",
        await repository.count_attempts(empty_filters, context) == 0,
        "a filter matching nothing must count zero rather than ignoring the filter",
    )

    return report


async def _drain_attempts(
    repository: AnalyticsRepository,
    filters: AnalyticsFilters,
    context: QueryContext,
    page_size: int,
):
    collected = []
    cursor: str | None = None
    for _ in range(10_000):  # guard against a provider that never ends its cursor
        page = await repository.fetch_attempts_page(
            filters, PageRequest(cursor=cursor, limit=page_size), context
        )
        collected.extend(page.items)
        if not page.has_more or not page.items:
            break
        cursor = page.next_cursor
    return collected, [a.attempt_id for a in collected]


async def _drain_responses(
    repository: AnalyticsRepository,
    filters: AnalyticsFilters,
    context: QueryContext,
    page_size: int,
    *,
    question_ids=None,
):
    collected = []
    cursor: str | None = None
    for _ in range(10_000):
        page = await repository.fetch_responses_page(
            filters,
            PageRequest(cursor=cursor, limit=page_size),
            context,
            question_ids=question_ids,
        )
        collected.extend(page.items)
        if not page.has_more or not page.items:
            break
        cursor = page.next_cursor
    return collected
