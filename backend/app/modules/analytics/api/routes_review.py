"""Content-review action routes (spec sections 11, 19)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.modules.analytics.api.deps import AdminDep, ContextDep, get_review_service
from app.modules.analytics.domain.review import (
    ReviewActionRequest,
    ReviewActionResponse,
    ReviewAuditPage,
    ReviewHistoryResponse,
)
from app.modules.analytics.services.review_service import ReviewService

# Namespaced under /analytics so the whole capability sits in one place on the admin surface:
# a bare /review at the admin root would read as the platform's review, not analytics'.
router = APIRouter(prefix="/analytics/review", tags=["Analytics content review (UC-10)"])

ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]


@router.post(
    "/actions",
    response_model=ReviewActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a content-review action",
    description=(
        "Records No Change, Question Updated or Question Retired against a "
        "question. The acting administrator is taken from the authenticated "
        "caller; an admin_id in the body that disagrees is rejected. The action "
        "is appended to an immutable audit log and transitions any existing flag: "
        "updates and no-change decisions resolve it, retirement is terminal."
    ),
)
async def record_review_action(
    service: ReviewServiceDep,
    context: ContextDep,
    admin: AdminDep,
    request: ReviewActionRequest,
) -> ReviewActionResponse:
    return await service.record_action(request, admin, context)


@router.get(
    "/actions",
    response_model=ReviewAuditPage,
    summary="List review actions",
    description="Audit log across questions, newest first.",
)
async def list_review_actions(
    service: ReviewServiceDep,
    context: ContextDep,
    _admin: AdminDep,
    question_id: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    admin_id: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewAuditPage:
    return await service.list_actions(
        context,
        question_id=question_id,
        admin_id=admin_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/questions/{question_id}/history",
    response_model=ReviewHistoryResponse,
    summary="Review history for one question",
    description="Every review decision recorded for the question, plus its current flag state.",
)
async def get_review_history(
    service: ReviewServiceDep,
    context: ContextDep,
    _admin: AdminDep,
    question_id: Annotated[str, Path(min_length=1, max_length=255)],
) -> ReviewHistoryResponse:
    return await service.get_history(question_id, context)
