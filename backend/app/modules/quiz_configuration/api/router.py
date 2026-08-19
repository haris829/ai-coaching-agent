"""Aggregate router for UC-01.

Mounted as one router so adding this capability to the API is a single ``include_router`` call and
there is no chance of a route collision with the question bank's endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.quiz_configuration.api import admin, learner, meta

quiz_configuration_router = APIRouter()

quiz_configuration_router.include_router(meta.router)
quiz_configuration_router.include_router(admin.router)
quiz_configuration_router.include_router(learner.router)
