"""The FastAPI application.

Error translation lives here, and it leaks nothing: an upstream failure becomes
a status code and a fixed sentence. No vendor name, no payload fragment, no
stack detail reaches a client.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from uc08.api.routes import health, streaks, weekly_summaries
from uc08.composition import Container, build_container
from uc08.domain.errors import (
    FreezeNotAvailable,
    ProviderError,
    RepositoryError,
    SessionIdRequired,
)
from uc08.logging_setup import get_logger
from uc08.ports.identity import IdentityNotResolved

_log = get_logger(__name__)

DESCRIPTION = """\
UC-08 Learning Streaks and Milestones.

Tracks consecutive-day coaching activity, awards milestone badges, and generates
a weekly summary. It reads activity and writes streaks, badges and weekly
summaries -- nothing else. No endpoint accepts a user identifier: the account is
resolved server-side.
"""


def create_app(container: Container | None = None) -> FastAPI:
    app = FastAPI(
        title="UC-08 Learning Streaks and Milestones",
        version="1.0.0",
        description=DESCRIPTION,
    )
    app.state.container = container or build_container()

    app.include_router(streaks.router)
    app.include_router(weekly_summaries.router)
    app.include_router(health.router)

    @app.exception_handler(IdentityNotResolved)
    def _identity_not_resolved(request: Request, exc: IdentityNotResolved) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"error": "identity_not_resolved", "detail": "authentication is required"},
        )

    @app.exception_handler(SessionIdRequired)
    def _session_required(request: Request, exc: SessionIdRequired) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": "session_id_required", "detail": str(exc)},
        )

    @app.exception_handler(FreezeNotAvailable)
    def _freeze_not_available(request: Request, exc: FreezeNotAvailable) -> JSONResponse:
        # An incentive feature refusing is not a coaching failure.
        return JSONResponse(
            status_code=409,
            content={"error": "freeze_not_available", "detail": str(exc)},
        )

    @app.exception_handler(ProviderError)
    def _provider_error(request: Request, exc: ProviderError) -> JSONResponse:
        # Services degrade rather than propagate, so reaching here is a bug.
        # It still must not leak the upstream.
        _log.error("unhandled_provider_error", extra={"port": exc.port, "error_type": type(exc).__name__})
        return JSONResponse(
            status_code=503,
            content={"error": "upstream_unavailable", "detail": "a required read model did not answer"},
        )

    @app.exception_handler(RepositoryError)
    def _repository_error(request: Request, exc: RepositoryError) -> JSONResponse:
        _log.error("unhandled_repository_error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=503,
            content={"error": "storage_unavailable", "detail": "the streak store did not answer"},
        )

    return app
