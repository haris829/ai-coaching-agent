"""Weekly summary endpoints.

Generation is an explicit callable operation. This component has no scheduler,
cron daemon or background worker: a caller drives the endpoint. See
``docs/INTEGRATION.md``.
"""

from __future__ import annotations

from fastapi import APIRouter

from uc08.api.deps import CurrentUser, WeeklySummaryServiceDep
from uc08.api.schemas import (
    GenerateWeeklySummaryRequest,
    GenerateWeeklySummaryResponse,
    WeeklySummaryCollectionResponse,
    weekly_summary_response,
)

router = APIRouter(prefix="/api/v1", tags=["weekly-summaries"])


@router.post("/weekly-summaries/generate", response_model=GenerateWeeklySummaryResponse)
def generate_weekly_summary(
    user_id: CurrentUser,
    summaries: WeeklySummaryServiceDep,
    payload: GenerateWeeklySummaryRequest = GenerateWeeklySummaryRequest(),
) -> GenerateWeeklySummaryResponse:
    result = summaries.generate(user_id)
    return GenerateWeeklySummaryResponse(
        generated=weekly_summary_response(result.generated) if result.generated else None,
        already_generated=result.already_generated,
        retried=weekly_summary_response(result.retried) if result.retried else None,
        reason=result.reason,
        skipped_weeks=result.skipped_weeks,
    )


@router.get("/weekly-summaries", response_model=WeeklySummaryCollectionResponse)
def list_weekly_summaries(
    user_id: CurrentUser,
    summaries: WeeklySummaryServiceDep,
) -> WeeklySummaryCollectionResponse:
    return WeeklySummaryCollectionResponse(
        summaries=tuple(weekly_summary_response(item) for item in summaries.list_for_user(user_id))
    )
