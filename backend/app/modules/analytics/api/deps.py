"""Dependency injection for the API layer.

Everything a route needs is resolved from a single :class:`ServiceContainer`
built once at application startup and stored on ``app.state``. Two consequences
matter:

* There is no module-level mutable state, no import-time singleton and no global
  repository. Two apps with different providers can coexist in one process, which
  is what makes the test suite able to inject failing and slow providers.
* The repository implementations are supplied from outside, so swapping the
  in-memory reference provider for the real assessment system touches exactly one
  call site and no service or route.

**The container is now built per request, not once at start-up.** Both of UC-10's repositories read
through a SQLAlchemy session, and a session is not safe to share across requests. The process-wide
half — the settings and the clock — lives on :class:`AnalyticsAppContext`, exactly as it does for
UC-04/05/06, UC-07, UC-08 and UC-09; this module builds the services around one request's session.

**Authentication is the application's.** UC-10 shipped its own API-key check because standalone it
had to. Every endpoint here now sits behind the same administrator guard UC-01, UC-02 and UC-08
use, and the actor an audit row records is the administrator that guard resolved — not a key
looked up in a map this module owned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query, Request

from app.core.deps import DbSession
from app.core.time import Clock, SystemClock
from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.domain.enums import AssessmentType, QuestionSortField, SortDirection
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.errors import ConfigurationError
from app.modules.analytics.repositories.base import AnalyticsRepository, ReviewRepository
from app.modules.analytics.services.analytics_service import AnalyticsService
from app.modules.analytics.services.export_service import CsvExportService
from app.modules.analytics.services.flag_service import FlagService
from app.modules.analytics.services.review_service import ReviewService
from app.modules.identity.security import Actor

__all__ = [
    "ServiceContainer",
    "build_container",
    "get_container",
    "get_settings",
    "get_analytics_service",
    "get_flag_service",
    "get_review_service",
    "get_export_service",
    "get_filters",
    "get_filters_excluding_course",
    "get_query_context",
    "require_admin",
    "QuestionListParams",
    "get_question_list_params",
]


@dataclass(frozen=True)
class ServiceContainer:
    """Fully wired services for one application instance."""

    settings: AnalyticsSettings
    clock: Clock
    analytics_repository: AnalyticsRepository
    review_repository: ReviewRepository
    analytics_service: AnalyticsService
    flag_service: FlagService
    review_service: ReviewService
    export_service: CsvExportService


def build_container(
    *,
    analytics_repository: AnalyticsRepository,
    review_repository: ReviewRepository,
    settings: AnalyticsSettings | None = None,
    clock: Clock | None = None,
) -> ServiceContainer:
    """Wire services over the supplied repositories."""
    settings = settings or AnalyticsSettings()
    clock = clock or SystemClock()

    analytics_service = AnalyticsService(analytics_repository, settings, clock)
    return ServiceContainer(
        settings=settings,
        clock=clock,
        analytics_repository=analytics_repository,
        review_repository=review_repository,
        analytics_service=analytics_service,
        flag_service=FlagService(
            analytics_service, analytics_repository, review_repository, settings, clock
        ),
        review_service=ReviewService(review_repository, settings, clock),
        export_service=CsvExportService(analytics_service, settings, clock),
    )


def get_container(request: Request, db: DbSession) -> ServiceContainer:
    """UC-10's services, bound to this request's session."""
    context = getattr(request.app.state, "analytics", None)
    if context is None:  # pragma: no cover - a wiring mistake, not a runtime condition
        raise ConfigurationError(
            "The application was created without an AnalyticsAppContext."
        )
    return context.build(db)


ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def get_settings(container: ContainerDep) -> AnalyticsSettings:
    return container.settings


def get_analytics_service(container: ContainerDep) -> AnalyticsService:
    return container.analytics_service


def get_flag_service(container: ContainerDep) -> FlagService:
    return container.flag_service


def get_review_service(container: ContainerDep) -> ReviewService:
    return container.review_service


def get_export_service(container: ContainerDep) -> CsvExportService:
    return container.export_service


def get_filters(
    course_id: Annotated[
        str | None, Query(description="Restrict to one course. Omit for platform-level analytics.")
    ] = None,
    cohort_id: Annotated[str | None, Query(description="Restrict to one learner group.")] = None,
    assessment_type: Annotated[
        AssessmentType | None, Query(description="Standard Quiz or Formal Assessment.")
    ] = None,
    start_date: Annotated[
        datetime | None,
        Query(description="Inclusive lower bound on attempt start time (ISO 8601)."),
    ] = None,
    end_date: Annotated[
        datetime | None,
        Query(description="Exclusive upper bound on attempt start time (ISO 8601)."),
    ] = None,
) -> AnalyticsFilters:
    """Build the shared filter object from query parameters.

    Constructed through the domain model, so date-range validation and UTC
    normalisation are identical for every endpoint and for the CSV exports.
    """
    return AnalyticsFilters(
        course_id=course_id,
        cohort_id=cohort_id,
        assessment_type=assessment_type,
        start_date=start_date,
        end_date=end_date,
    )


def get_filters_excluding_course(
    cohort_id: Annotated[str | None, Query(description="Restrict to one learner group.")] = None,
    assessment_type: Annotated[
        AssessmentType | None, Query(description="Standard Quiz or Formal Assessment.")
    ] = None,
    start_date: Annotated[
        datetime | None,
        Query(description="Inclusive lower bound on attempt start time (ISO 8601)."),
    ] = None,
    end_date: Annotated[
        datetime | None,
        Query(description="Exclusive upper bound on attempt start time (ISO 8601)."),
    ] = None,
) -> AnalyticsFilters:
    """Filters for routes that take the course from the path.

    ``course_id`` is omitted deliberately: FastAPI would otherwise read the
    dependency's query parameter and the path parameter as the same name, and a
    request could then disagree with itself about which course it is asking about.
    """
    return AnalyticsFilters(
        cohort_id=cohort_id,
        assessment_type=assessment_type,
        start_date=start_date,
        end_date=end_date,
    )


FiltersDep = Annotated[AnalyticsFilters, Depends(get_filters)]
CourseScopedFiltersDep = Annotated[AnalyticsFilters, Depends(get_filters_excluding_course)]


def get_query_context(request: Request, container: ContainerDep) -> QueryContext:
    """Create a per-request query context.

    The context carries the configured timeout and a stop check wired to the
    client connection, so abandoning a request stops the aggregation instead of
    leaving it to finish into a socket nobody is reading (spec section 15).
    """
    context = QueryContext.create(
        timeout_seconds=container.settings.query_timeout_seconds,
        clock=container.clock,
        request_id=getattr(request.state, "request_id", None),
    )
    context.add_stop_check(request.is_disconnected)
    return context


ContextDep = Annotated[QueryContext, Depends(get_query_context)]


def require_admin(actor: Actor) -> str:
    """The administrator every analytics read and every review action is attributed to.

    Returns the actor string the shared guard resolved. UC-10 previously returned an
    ``AdminPrincipal`` of its own construction; the audit row only ever needed the identifier, and
    having one fewer definition of "who is an admin" is the point of the merge.
    """
    return actor


AdminDep = Annotated[str, Depends(require_admin)]


@dataclass(frozen=True)
class QuestionListParams:
    """Pagination and ordering for question listings."""

    limit: int
    offset: int
    sort_by: QuestionSortField
    direction: SortDirection
    flagged_only: bool


def get_question_list_params(
    limit: Annotated[int, Query(ge=1, le=1000, description="Maximum questions to return.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Questions to skip.")] = 0,
    sort_by: Annotated[
        QuestionSortField, Query(description="Ordering key. Null metrics always sort last.")
    ] = QuestionSortField.QUESTION_ID,
    direction: Annotated[SortDirection, Query(description="Sort direction.")] = SortDirection.ASC,
    flagged_only: Annotated[
        bool, Query(description="Return only questions with an active persisted flag.")
    ] = False,
) -> QuestionListParams:
    return QuestionListParams(
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        direction=direction,
        flagged_only=flagged_only,
    )


QuestionListDep = Annotated[QuestionListParams, Depends(get_question_list_params)]
