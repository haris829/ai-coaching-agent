"""Aggregate router for UC-03.

Registration order is significant: literal path segments must come before parameterised ones, so
``/attempts/active`` is matched before ``/attempts/{attempt_id}``.

Mounted as one router, so adding this capability to the API is a single ``include_router`` call and
there is no chance of a route collision with UC-01's or UC-02's endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.attempt_delivery.api.routers import (
    answers,
    attempts,
    flags,
    questions,
    submission,
)

attempt_delivery_router = APIRouter()

attempt_delivery_router.include_router(attempts.router)
attempt_delivery_router.include_router(submission.router)
attempt_delivery_router.include_router(questions.router)
attempt_delivery_router.include_router(answers.router)
attempt_delivery_router.include_router(flags.router)
