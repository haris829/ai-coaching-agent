"""UC-01 HTTP endpoints.

Six endpoints, one per capability the brief calls for, plus a health check and a
dev-only helper for the reference UI:

======================================== ===================================================
``GET  /api/v1/session-bootstrap``       load session-opening data + inspect available modes
``GET  /api/v1/courses``                 retrieve courses (with their lessons)
``GET  /api/v1/case-files``              retrieve accessible case files
``POST /api/v1/sessions``                create/open a coaching session
``GET  /api/v1/sessions/{session_id}``   read one own session (ownership enforced)
``GET  /api/v1/healthz``                 liveness + which adapters are in use
``GET  /api/v1/dev/context``             dev-only: dev users and mock scenario options
======================================== ===================================================

No endpoints exist for UC-02..UC-10.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request, Response, status

from ..adapters.mock.scenarios import (
    CaseScenario,
    CoursesScenario,
    NaricScenario,
    ProfileScenario,
)
from ..application.dto import OpenSessionCommand
from ..domain.errors import SessionNotFoundError
from .deps import CurrentUser, Service, SettingsDep, integrations_notice
from .schemas import (
    BootstrapResponse,
    CaseFilesResponse,
    CoursesResponse,
    DevContextResponse,
    ErrorResponse,
    HealthResponse,
    OpenSessionRequest,
    OpenSessionResponse,
    SessionOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["UC-01 Coaching Session Initiation"])

_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid selection for the chosen mode."},
    401: {"model": ErrorResponse, "description": "Caller could not be identified."},
    403: {"model": ErrorResponse, "description": "Selection is not accessible to this user."},
    409: {"model": ErrorResponse, "description": "Requested session mode is unavailable."},
    422: {"model": ErrorResponse, "description": "Request body failed validation."},
    503: {"model": ErrorResponse, "description": "A required dependency is unavailable."},
}


@router.get(
    "/session-bootstrap",
    response_model=BootstrapResponse,
    responses={401: _ERROR_RESPONSES[401]},
    summary="Load everything needed to open the coaching interface",
)
def session_bootstrap(
    user: CurrentUser,
    service: Service,
    settings: SettingsDep,
    continue_without_calibration: bool = Query(
        default=False,
        description=(
            "Reflects the user's 'Continue without calibration' choice so the preview "
            "and notices match what opening the session will do."
        ),
    ),
) -> BootstrapResponse:
    """Mode availability, catalogues, NARIC state, notices and a greeting preview.

    Never fails because a dependency is down: an outage is reported as availability
    metadata so the rest of the interface stays usable.
    """
    result = service.load_bootstrap(
        user, continue_without_calibration=continue_without_calibration
    )
    return BootstrapResponse.of(result, integrations_notice(settings))


@router.get(
    "/courses",
    response_model=CoursesResponse,
    responses={401: _ERROR_RESPONSES[401]},
    summary="Courses accessible to the caller, each with its lessons",
)
def list_courses(user: CurrentUser, service: Service) -> CoursesResponse:
    """Returns 200 with ``available=false`` and a reason when the Courses Agent is down,
    so the picker can render a disabled state rather than an error page."""
    return CoursesResponse.of(service.list_courses(user))


@router.get(
    "/case-files",
    response_model=CaseFilesResponse,
    responses={401: _ERROR_RESPONSES[401]},
    summary="Case files accessible to the caller",
)
def list_case_files(user: CurrentUser, service: Service) -> CaseFilesResponse:
    """Returns 200 with ``available=false`` when there are no accessible case files or
    the service is down. Absence of case files is never fatal for the interface."""
    return CaseFilesResponse.of(service.list_case_files(user))


@router.post(
    "/sessions",
    response_model=OpenSessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
    summary="Open a coaching session",
)
def open_session(
    payload: OpenSessionRequest,
    user: CurrentUser,
    service: Service,
) -> OpenSessionResponse:
    """Create and open a session.

    A session record is written for **every** attempt, before any dependency is
    contacted — including attempts that are then rejected (unavailable mode,
    inaccessible course/case) or that fail unexpectedly.
    """
    command = OpenSessionCommand(
        mode=payload.mode,
        course_id=payload.course_id,
        lesson_id=payload.lesson_id,
        case_id=payload.case_id,
        continue_without_calibration=payload.continue_without_calibration,
        dependency_failure_policy=payload.on_dependency_failure,
    )
    return OpenSessionResponse.of(service.open_session(user, command))


@router.get(
    "/sessions/{session_id}",
    response_model=SessionOut,
    responses={401: _ERROR_RESPONSES[401], 404: {"model": ErrorResponse}},
    summary="Read one of the caller's own sessions",
)
def get_session(session_id: str, user: CurrentUser, service: Service) -> SessionOut:
    """Another user's session is reported as *not found*, never as forbidden, so session
    ids cannot be probed."""
    return SessionOut.of(service.get_session(user, session_id))


@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Liveness and which adapters are wired in",
)
def healthz(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        use_case="UC-01 Coaching Session Initiation",
        environment=settings.environment,
        persistence=settings.persistence,
        integrations=integrations_notice(settings),
    )


@router.get(
    "/dev/context",
    response_model=DevContextResponse,
    summary="Development helper: dev users and mock scenario options",
)
def dev_context(request: Request, settings: SettingsDep, response: Response):
    """Powers the reference UI's user switcher and scenario panel.

    Returns 404 when dev mode is off, so it does not exist in a non-development
    deployment.
    """
    if not settings.dev_mode:
        raise SessionNotFoundError("That resource could not be found.")

    provider = request.app.state.container.identity
    directory = getattr(provider, "dev_directory", None)
    users = (
        [
            {"user_id": user_id, "token": values["token"], "label": values["label"]}
            for user_id, values in directory().items()
        ]
        if callable(directory)
        else []
    )
    return DevContextResponse(
        users=users,
        scenarios=settings.scenarios.describe(),
        scenario_options={
            "naric": [member.value for member in NaricScenario],
            "courses": [member.value for member in CoursesScenario],
            "cases": [member.value for member in CaseScenario],
            "profile": [member.value for member in ProfileScenario],
        },
        scenario_header_enabled=settings.dev_scenario_header_enabled,
    )


__all__ = ["router"]
