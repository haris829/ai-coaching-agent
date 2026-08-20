"""Question analytics and content-review flag routes (spec sections 7, 8, 19)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.modules.analytics.api.deps import (
    AdminDep,
    ContextDep,
    FiltersDep,
    QuestionListDep,
    get_analytics_service,
    get_flag_service,
)
from app.modules.analytics.domain.analytics import (
    FlagEvaluationResult,
    FlaggedQuestionsResponse,
    QuestionAnalyticsPage,
    QuestionAnalyticsResponse,
)
from app.modules.analytics.services.analytics_service import AnalyticsService
from app.modules.analytics.services.flag_service import FlagService

router = APIRouter(prefix="/analytics/questions", tags=["questions"])

AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]
FlagServiceDep = Annotated[FlagService, Depends(get_flag_service)]


@router.get(
    "",
    response_model=QuestionAnalyticsPage,
    summary="Question analytics",
    description=(
        "Per-question accuracy, counts, most frequent wrong answer, average time "
        "and flag status for the filtered scope. Questions whose metrics are null "
        "for lack of data always sort last, whatever the sort direction."
    ),
)
async def list_question_analytics(
    service: AnalyticsServiceDep,
    filters: FiltersDep,
    context: ContextDep,
    params: QuestionListDep,
    _admin: AdminDep,
) -> QuestionAnalyticsPage:
    return await service.list_question_analytics(
        filters,
        context,
        limit=params.limit,
        offset=params.offset,
        sort_by=params.sort_by,
        direction=params.direction,
        flagged_only=params.flagged_only,
    )


# Declared before /{question_id} so that "flagged" is never parsed as an id.
@router.get(
    "/flagged",
    response_model=FlaggedQuestionsResponse,
    summary="Flagged questions",
    description=(
        "Content-review queue. Returns questions with an active persisted flag, "
        "including those with no responses in the filtered scope so that a flag "
        "never appears to vanish when filters change. Set include_candidates=true "
        "to also see questions that currently breach the threshold but have no "
        "persisted flag yet; this remains a read-only operation and writes nothing."
    ),
)
async def list_flagged_questions(
    service: AnalyticsServiceDep,
    filters: FiltersDep,
    context: ContextDep,
    _admin: AdminDep,
    include_candidates: Annotated[
        bool,
        Query(description="Also list unpersisted questions that currently breach the threshold."),
    ] = False,
) -> FlaggedQuestionsResponse:
    return await service.get_flagged_questions(
        filters, context, include_candidates=include_candidates
    )


@router.post(
    "/flags/evaluate",
    response_model=FlagEvaluationResult,
    summary="Evaluate and persist content-review flags",
    description=(
        "Evaluates every question in the filtered scope against the configured "
        "threshold and persists new flags. This is the only analytics operation "
        "that writes, and it writes solely to the review store; assessment data is "
        "never modified. Existing flags are never re-raised or cleared here: a rate "
        "that has fallen below the threshold leaves its flag standing, and only a "
        "review action can resolve one."
    ),
)
async def evaluate_flags(
    service: FlagServiceDep,
    filters: FiltersDep,
    context: ContextDep,
    admin: AdminDep,
) -> FlagEvaluationResult:
    return await service.evaluate(filters, context, triggered_by=admin)


@router.get(
    "/{question_id}",
    response_model=QuestionAnalyticsResponse,
    summary="Single question analytics",
    description=(
        "Analytics for one question. A known question with no responses in scope "
        "returns data_state=NO_ATTEMPTS with null metrics; an unknown identifier "
        "returns 404."
    ),
)
async def get_question_analytics(
    service: AnalyticsServiceDep,
    filters: FiltersDep,
    context: ContextDep,
    _admin: AdminDep,
    question_id: Annotated[str, Path(min_length=1, max_length=255)],
) -> QuestionAnalyticsResponse:
    return await service.get_question_analytics(question_id, filters, context)
