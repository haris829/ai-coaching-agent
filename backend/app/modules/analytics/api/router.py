"""UC-10's HTTP surface, mounted under the administrator root.

Standalone, UC-10 mounted its own ``/api/v1`` and owned the whole prefix. In the merged application
analytics is an **administrator** capability — every endpoint reads or reviews aggregate data, and
none of it is learner-facing — so it joins the ``/api/admin`` surface UC-01, UC-02 and UC-08's
grants already use, under ``/analytics``.

That is not only tidiness. ``/api/v1`` is the *learner* conversation in this application, and a
route mounted there is a route a learner's client can be pointed at. Analytics belongs behind the
administrator guard and its address should say so.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.analytics.api import (
    routes_analytics,
    routes_config,
    routes_export,
    routes_questions,
    routes_review,
)

analytics_router = APIRouter()
analytics_router.include_router(routes_analytics.router)
analytics_router.include_router(routes_questions.router)
analytics_router.include_router(routes_export.router)
analytics_router.include_router(routes_review.router)
analytics_router.include_router(routes_config.router)
