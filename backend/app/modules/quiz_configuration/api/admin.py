"""Admin configuration API.

    GET  /quizzes                                → quizzes to configure
    GET  /quizzes/{quiz_id}/configuration        → active configuration + live capacity
    PUT  /quizzes/{quiz_id}/configuration        → new immutable version (201) / no-op (200)
    GET  /quizzes/{quiz_id}/configuration/versions → immutable version history
    GET  /quizzes/{quiz_id}/question-bank        → live eligible-question counts per type
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Response, status

from app.core.schemas import ErrorResponse
from app.modules.identity.security import AdminPrincipal
from app.modules.quiz_configuration.api import serializers
from app.modules.quiz_configuration.context import ContextDep
from app.modules.quiz_configuration.services import configuration_service

router = APIRouter(
    prefix="/admin",
    tags=["Quiz Configuration — Admin"],
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Admin role required"},
        404: {"model": ErrorResponse, "description": "Quiz not found"},
        422: {"model": ErrorResponse, "description": "Configuration failed validation"},
        503: {"model": ErrorResponse, "description": "Nothing was saved — safe to retry"},
    },
)

CONFIGURATION_EXAMPLE = {
    "questionCount": 20,
    "timeLimitMinutes": 30,
    "passMark": 70,
    "maxAttempts": 2,
    "deliveryMode": "assessment",
    "randomiseQuestions": True,
    "questionTypes": [
        {"type": "SINGLE_CHOICE", "quota": 10},
        {"type": "TRUE_FALSE", "quota": 10},
    ],
    "topicIds": [],
}


@router.get("/quizzes", summary="Quizzes available to configure")
def list_quizzes(ctx: ContextDep, _admin: AdminPrincipal) -> dict[str, Any]:
    return {"quizzes": [serializers.quiz_summary(quiz) for quiz in ctx.quizzes.list_all()]}


@router.get(
    "/quizzes/{quiz_id}/configuration",
    summary="Active configuration and a live question-bank capacity report",
)
def get_configuration(quiz_id: int, ctx: ContextDep, _admin: AdminPrincipal) -> dict[str, Any]:
    return configuration_service.get_configuration(ctx, quiz_id)


@router.put(
    "/quizzes/{quiz_id}/configuration",
    summary="Save the configuration by creating a new immutable version",
)
def save_configuration(
    quiz_id: int,
    ctx: ContextDep,
    admin: AdminPrincipal,
    response: Response,
    payload: Annotated[
        dict[str, Any],
        Body(description="Quiz configuration settings.", examples=[CONFIGURATION_EXAMPLE]),
    ],
) -> dict[str, Any]:
    body, created = configuration_service.save_configuration(
        ctx, quiz_id, payload, actor_user_id=admin.id or None, actor=admin.actor
    )
    # 201 when a new immutable version was created, 200 when the save was a no-op.
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return body


@router.get(
    "/quizzes/{quiz_id}/configuration/versions",
    summary="Immutable configuration version history, newest first",
)
def list_versions(quiz_id: int, ctx: ContextDep, _admin: AdminPrincipal) -> dict[str, Any]:
    return configuration_service.list_versions(ctx, quiz_id)


@router.get(
    "/quizzes/{quiz_id}/question-bank",
    summary="Eligible question counts per type — retired questions never appear",
)
def get_question_bank(
    quiz_id: int,
    ctx: ContextDep,
    _admin: AdminPrincipal,
    topic_id: Annotated[
        list[str] | None,
        Query(alias="topicId", description="Optional topic scope, repeatable"),
    ] = None,
) -> dict[str, Any]:
    return configuration_service.get_question_bank_availability(
        ctx, quiz_id, tuple(topic_id or ())
    )
