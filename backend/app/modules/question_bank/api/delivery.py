"""Delivery + historical reporting endpoints — the integration seam (UC-02 §16, §21).

    GET   /delivery/pool                    questions eligible for a FUTURE quiz (ACTIVE only)
    POST  /delivery/usages                  record "this question was delivered to this attempt"
    PATCH /delivery/usages/{id}             record the learner's response / complete it
    GET   /reporting/attempts/{attemptRef}  historical report, rendered from frozen snapshots

The quiz-delivery / attempt module (built separately) is the intended consumer. UC-02 does not
own attempts — see ``QuestionUsage`` in models.py for why ``attemptRef`` is not a foreign key.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, status

from app.core.deps import DbSession
from app.core.schemas import ErrorResponse
from app.modules.identity.security import Actor
from app.modules.question_bank.api import serializers
from app.modules.question_bank.schemas.delivery import (
    DeliveryPoolResponse,
    RecordResponseRequest,
    RecordUsageRequest,
    UsageOut,
)
from app.modules.question_bank.services import delivery_service

router = APIRouter(
    tags=["Question Bank — Delivery & Reporting"],
    responses={
        404: {"model": ErrorResponse, "description": "Not found"},
        409: {"model": ErrorResponse, "description": "Not deliverable, or already recorded"},
        422: {"model": ErrorResponse, "description": "Response failed validation"},
    },
)



@router.get(
    "/delivery/pool",
    summary="Questions eligible for future delivery — retired questions can never appear",
    response_model=DeliveryPoolResponse,
)
def delivery_pool(
    db: DbSession,
    actor: Actor,
    topic_id: Annotated[list[str] | None, Query(alias="topicId")] = None,
    topic_slug: Annotated[list[str] | None, Query(alias="topicSlug")] = None,
    type: Annotated[list[str] | None, Query()] = None,
    difficulty: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 10,
) -> DeliveryPoolResponse:
    questions, total = delivery_service.select_deliverable_questions(
        db,
        topic_ids=topic_id,
        topic_slugs=topic_slug,
        types=type,
        difficulty=difficulty,
        limit=limit,
    )
    return DeliveryPoolResponse(
        items=[serializers.deliverable_question(question) for question in questions],
        total_available=total,
        requested=limit,
    )


@router.post(
    "/delivery/usages",
    summary="Record that a question was delivered to an attempt (pins its snapshot)",
    status_code=status.HTTP_201_CREATED,
    response_model=UsageOut,
)
def record_usage(
    db: DbSession, actor: Actor, payload: Annotated[RecordUsageRequest, Body()]
) -> UsageOut:
    usage = delivery_service.record_usage(
        db,
        attempt_ref=payload.attempt_ref,
        question_id=payload.question_id,
        learner_ref=payload.learner_ref,
        presentation_order=payload.presentation_order,
    )
    return serializers.usage_out(usage)


@router.patch(
    "/delivery/usages/{usage_id}",
    summary="Record the learner's response and score it against the pinned snapshot",
    response_model=UsageOut,
)
def record_response(
    db: DbSession,
    actor: Actor,
    usage_id: str,
    payload: Annotated[RecordResponseRequest, Body()],
) -> UsageOut:
    usage = delivery_service.record_response(
        db,
        usage_id,
        selected_labels=payload.selected_labels,
        ordered_labels=payload.ordered_labels,
        attempt_status=payload.attempt_status,
    )
    return serializers.usage_out(usage)


@router.get(
    "/reporting/attempts/{attempt_ref}",
    summary="Historical attempt report — rendered from frozen snapshots, survives retirement",
    response_model=dict,
)
def attempt_report(db: DbSession, actor: Actor, attempt_ref: str) -> dict[str, Any]:
    return delivery_service.build_attempt_report(db, attempt_ref)
