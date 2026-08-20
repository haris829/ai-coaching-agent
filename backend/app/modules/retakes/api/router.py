"""UC-08's HTTP surface — two routers, because it serves two audiences.

Standalone, UC-08 mounted everything under one ``/retakes`` prefix. In the merged application the
two halves belong in the two places the application already puts them:

* the learner half joins UC-03's versioned learner conversation, under ``/api/v1`` — a retake *is*
  a new attempt, and a client that already talks to ``/v1/attempts`` should not have to learn a
  second root to ask for another one;
* the administrator half joins the admin surface UC-01 and UC-02 use, under ``/api/admin``.

Splitting them is not cosmetic: the two carry different authentication (a learner bearer token
against the administrator guard), and shipping them under one prefix invited exactly the mistake
of treating one audience's route as the other's.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.retakes.api import grants, retakes

#: Learner-facing. Mounted by ``app.main`` under ``/api/v1``, beside UC-03's attempt router.
retakes_router = APIRouter()
retakes_router.include_router(retakes.router)

#: Administrator-facing. Mounted by ``app.main`` under ``/api/admin/retakes``.
retake_admin_router = APIRouter()
retake_admin_router.include_router(grants.router)
