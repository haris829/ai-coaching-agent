"""Learner-facing configuration API.

    GET /quizzes                    → quizzes a learner can see
    GET /quizzes/{quiz_id}/rules    → rules summary + remaining attempts (READ ONLY)

**Starting and running an attempt lives in UC-03**, under ``/api/v1``:

    GET  /api/v1/quizzes/{quizId}/attempt-eligibility
    POST /api/v1/attempts
    GET  /api/v1/attempts/{id}/questions   … answers, flags, timing, submission

Before UC-03 existed, UC-01 carried a provisional ``POST /quizzes/{id}/attempts`` so that "start
quiz" could be demonstrated. UC-03's implementation supersedes it entirely, so the endpoint is gone
rather than left as a second way to consume an attempt allowance. Nothing here writes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.schemas import ErrorResponse
from app.modules.identity.security import CurrentPrincipal
from app.modules.quiz_configuration.api import serializers
from app.modules.quiz_configuration.context import ContextDep
from app.modules.quiz_configuration.services import rules_service

router = APIRouter(
    tags=["Quiz Configuration — Learner"],
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Quiz not found"},
        409: {"model": ErrorResponse, "description": "The quiz has not been configured"},
    },
)


@router.get("/quizzes", summary="Quizzes a learner can see")
def list_quizzes(ctx: ContextDep, _user: CurrentPrincipal) -> dict[str, Any]:
    return {"quizzes": [serializers.quiz_summary(quiz) for quiz in ctx.quizzes.list_all()]}


@router.get(
    "/quizzes/{quiz_id}/rules",
    summary="Rules summary and remaining attempts — creates nothing",
)
def get_rules(quiz_id: int, ctx: ContextDep, user: CurrentPrincipal) -> dict[str, Any]:
    return rules_service.get_rules(ctx, quiz_id, user)
