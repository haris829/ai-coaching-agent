"""UC-07 HTTP routes.

* ``GET /api/v1/gap-report``          - current report, or progress when below threshold
* ``GET /api/v1/gap-report/progress`` - progress towards the threshold
* ``GET /api/v1/healthz``             - liveness plus versions

No endpoint accepts a user id, in any position.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from uc07 import ANALYSIS_VERSION, REPORT_VERSION
from uc07.api.dependencies import (
    get_container,
    get_current_user,
    get_service,
    reject_request_input,
)
from uc07.api.schemas import (
    ErrorEnvelopeOut,
    GapReportEnvelopeOut,
    HealthOut,
    ProgressOut,
    progress_out,
    report_out,
)
from uc07.application.service import GapReportService
from uc07.composition import Container

router = APIRouter(prefix="/api/v1")

_ERROR_RESPONSES = {
    400: {"model": ErrorEnvelopeOut},
    401: {"model": ErrorEnvelopeOut},
    403: {"model": ErrorEnvelopeOut},
    503: {"model": ErrorEnvelopeOut},
}


@router.get(
    "/gap-report",
    response_model=GapReportEnvelopeOut,
    responses=_ERROR_RESPONSES,
    summary="Current knowledge-gap report for the resolved learner",
)
def get_gap_report(
    _: None = Depends(reject_request_input),
    user_id: str = Depends(get_current_user),
    service: GapReportService = Depends(get_service),
) -> GapReportEnvelopeOut:
    outcome = service.current_report(user_id)
    return GapReportEnvelopeOut(
        status=outcome.progress.status.value,
        interactions_completed=outcome.progress.interactions_completed,
        threshold=outcome.progress.threshold,
        interactions_remaining=outcome.progress.interactions_remaining,
        report=None if outcome.report is None else report_out(outcome.report),
    )


@router.get(
    "/gap-report/progress",
    response_model=ProgressOut,
    responses=_ERROR_RESPONSES,
    summary="Progress towards the gap-report threshold",
)
def get_progress(
    _: None = Depends(reject_request_input),
    user_id: str = Depends(get_current_user),
    service: GapReportService = Depends(get_service),
) -> ProgressOut:
    return progress_out(service.progress(user_id))


@router.get("/healthz", response_model=HealthOut, summary="Liveness and versions")
def healthz(container: Container = Depends(get_container)) -> HealthOut:
    return HealthOut(
        status="ok",
        report_version=REPORT_VERSION,
        analysis_version=ANALYSIS_VERSION,
        threshold=container.settings.gap_report_threshold,
    )
