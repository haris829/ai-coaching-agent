"""Dashboard analytics routes (spec sections 6, 19).

Routes stay thin on purpose: resolve dependencies, call one service method,
return the model. No arithmetic, no filtering, no branching on data.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.modules.analytics.api.deps import (
    AdminDep,
    ContextDep,
    CourseScopedFiltersDep,
    FiltersDep,
    get_analytics_service,
)
from app.modules.analytics.domain.analytics import OverallAnalytics
from app.modules.analytics.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics & Reporting (UC-10)"])

AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


@router.get(
    "/overall",
    response_model=OverallAnalytics,
    summary="Overall analytics",
    description=(
        "Average score, pass rate, completion rate and attempt volume for the "
        "filtered scope. Platform-level when no course filter is supplied, "
        "course-level when one is. Returns data_state=NO_ATTEMPTS with null "
        "metrics when nothing matches the filters."
    ),
)
async def get_overall_analytics(
    service: AnalyticsServiceDep,
    filters: FiltersDep,
    context: ContextDep,
    _admin: AdminDep,
) -> OverallAnalytics:
    return await service.get_overall_analytics(filters, context)


@router.get(
    "/courses/{course_id}/overall",
    response_model=OverallAnalytics,
    summary="Course-level overall analytics",
    description=(
        "Convenience form of /analytics/overall with the course taken from the "
        "path. All other filters are accepted as query parameters."
    ),
)
async def get_course_overall_analytics(
    service: AnalyticsServiceDep,
    filters: CourseScopedFiltersDep,
    context: ContextDep,
    _admin: AdminDep,
    course_id: Annotated[str, Path(min_length=1, max_length=255)],
) -> OverallAnalytics:
    return await service.get_overall_analytics(filters.with_course(course_id), context)
