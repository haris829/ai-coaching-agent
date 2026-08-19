"""UC-07's router assembly.

One ``include_router`` mounts the whole capability. It carries no prefix of its own: the paths in
``coaching.py`` are already rooted at ``/attempts/{id}/coaching/…`` and ``/coaching/sessions/…``,
and ``app.main`` mounts it under ``/api/v1`` alongside UC-03's, UC-04's, UC-05's and UC-06's routers
— so the coaching endpoints sit in the same versioned learner conversation about one attempt.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.coaching.api import coaching

coaching_router = APIRouter()
coaching_router.include_router(coaching.router)
