"""The generation capability's router.

Mounted under ``/api/v1`` in ``app.main``, so its paths sit beside UC-03's attempt routes rather
than in a namespace of their own — a generated quiz is a quiz.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.quiz_generation.api import quizzes

quiz_generation_router = APIRouter()
quiz_generation_router.include_router(quizzes.router)
# Its own prefix, so the course list cannot be shadowed by `/generated-quizzes/{quiz_id}` — see the
# note beside `catalogue_router`.
quiz_generation_router.include_router(quizzes.catalogue_router)
