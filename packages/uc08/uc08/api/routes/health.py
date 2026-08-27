"""Liveness. Reports the wiring, never a secret and never an upstream detail."""

from __future__ import annotations

from fastapi import APIRouter

from uc08.api.deps import ContainerDep
from uc08.api.schemas import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz(container: ContainerDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        component="uc08-learning-streaks",
        now=container.clock.now(),
        activity_provider=container.settings.activity_provider,
        gap_report_provider=container.settings.gap_report_provider,
        persistence=container.settings.persistence.value,
    )
