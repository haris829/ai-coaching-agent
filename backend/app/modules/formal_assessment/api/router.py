"""UC-09's HTTP surface — three routers, because it serves three audiences.

Standalone, UC-09 mounted everything under one ``/formal-assessments`` prefix. In the merged
application each half joins the surface that audience already talks to:

* the learner half joins UC-03's versioned learner conversation, under ``/api/v1`` — sitting a
  formal assessment *is* sitting an attempt, and a learner client should not have to learn a
  second root to do it;
* the assessor half gets its own ``/api/assessor`` root, because assessors are a third audience
  with their own credential, not administrators and not learners;
* the system half sits under ``/api/system``, reachable only with the service credential.

Splitting them is not cosmetic. The three carry three different guards, and shipping them under a
single prefix invited exactly the mistake of treating one audience's route as another's.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.formal_assessment.api import assessor, formal_attempts, system

#: Learner-facing. Mounted by ``app.main`` under ``/api/v1``, beside UC-03's attempt router.
formal_assessment_router = APIRouter()
formal_assessment_router.include_router(formal_attempts.router)

#: Assessor-facing. Mounted under ``/api/assessor``.
formal_assessor_router = APIRouter()
formal_assessor_router.include_router(assessor.router)

#: Platform-internal. Mounted under ``/api/system/formal-assessments``.
formal_system_router = APIRouter()
formal_system_router.include_router(system.router)
