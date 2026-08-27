"""Request-scoped dependencies.

FastAPI ``Depends`` plus the container assembled in :mod:`uc08.composition`.
No DI framework.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from uc08.application.badge_service import BadgeService
from uc08.application.streak_service import StreakService
from uc08.application.weekly_summary_service import WeeklySummaryService
from uc08.composition import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_current_user(request: Request, container: ContainerDep) -> str:
    """Resolve the account server-side.

    The only input is the request context. Nothing a client can put in a path,
    query string or body reaches this.
    """
    return container.identity.resolve(request)


CurrentUser = Annotated[str, Depends(get_current_user)]


def get_streak_service(container: ContainerDep) -> StreakService:
    return container.streak_service


def get_badge_service(container: ContainerDep) -> BadgeService:
    return container.badge_service


def get_weekly_summary_service(container: ContainerDep) -> WeeklySummaryService:
    return container.weekly_summary_service


StreakServiceDep = Annotated[StreakService, Depends(get_streak_service)]
BadgeServiceDep = Annotated[BadgeService, Depends(get_badge_service)]
WeeklySummaryServiceDep = Annotated[WeeklySummaryService, Depends(get_weekly_summary_service)]
