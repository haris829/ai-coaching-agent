"""FastAPI application for UC-02: Contextual Awareness Setup.

Run locally:  uvicorn uc02.main:app --reload

No frontend is served and none exists. Behaviour is demonstrated through the
API, the test suite and the fixtures.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from uc02.api.v1.context import router as context_router
from uc02.domain.errors import (
    ContextAccessDenied,
    ContextNotFound,
    ForceRefreshNotPermitted,
    IdentityResolutionFailed,
    ProviderNotImplemented,
    SessionIdRequired,
)
from uc02.domain.models.context import CONTEXT_VERSION
from uc02.infrastructure.config.settings import Settings, get_settings
from uc02.infrastructure.logging.setup import configure_logging, get_logger


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    log = get_logger()

    violations = settings.production_guard_violations()
    if violations:
        # Loud, not fatal: the operator sees exactly which switch is unsafe.
        log.error("config.production_guard.violation", extra={"violations": violations})

    application = FastAPI(
        title="UC-02 Contextual Awareness Setup",
        version="1.0.0",
        description=(
            "Assembles a learner's context at session start from four upstream "
            "sources. Internal service-to-service API. UC-02 receives session ids; "
            "it does not create them."
        ),
    )
    application.include_router(context_router)

    @application.get("/health", tags=["ops"])
    async def health() -> dict[str, object]:
        """Liveness plus the configuration an operator needs to sanity-check."""
        return {
            "status": "ok",
            "context_version": CONTEXT_VERSION,
            "providers": {
                "naric": settings.naric_provider,
                "courses": settings.courses_provider,
                "legal": settings.legal_provider,
                "history": settings.history_provider,
            },
            "guards": {
                "allow_dev_session_ids": settings.allow_dev_session_ids,
                "debug_context_endpoint": settings.debug_context_endpoint,
                "allow_force_refresh": settings.allow_force_refresh,
            },
        }

    _register_error_handlers(application)
    return application


def _register_error_handlers(application: FastAPI) -> None:
    """Domain errors -> HTTP.

    ``ContextNotFound`` and ``ContextAccessDenied`` both answer 404 on purpose:
    a caller must not be able to tell the difference between "no such session"
    and "that session belongs to someone else".
    """

    def _json(status_code: int, error: str, detail: str) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})

    @application.exception_handler(IdentityResolutionFailed)
    async def _identity_failed(request: Request, exc: IdentityResolutionFailed) -> JSONResponse:
        return _json(401, "unauthenticated", str(exc))

    @application.exception_handler(SessionIdRequired)
    async def _session_required(request: Request, exc: SessionIdRequired) -> JSONResponse:
        return _json(400, "session_id_required", str(exc))

    @application.exception_handler(ForceRefreshNotPermitted)
    async def _force_refresh(request: Request, exc: ForceRefreshNotPermitted) -> JSONResponse:
        return _json(403, "force_refresh_not_permitted", str(exc))

    @application.exception_handler(ContextNotFound)
    async def _not_found(request: Request, exc: ContextNotFound) -> JSONResponse:
        return _json(404, "context_not_found", "No context for that session id.")

    @application.exception_handler(ContextAccessDenied)
    async def _denied(request: Request, exc: ContextAccessDenied) -> JSONResponse:
        # Same body as not-found: never confirm that another user's session exists.
        return _json(404, "context_not_found", "No context for that session id.")

    @application.exception_handler(ProviderNotImplemented)
    async def _not_implemented(request: Request, exc: ProviderNotImplemented) -> JSONResponse:
        return _json(503, "provider_not_implemented", str(exc))


app = create_app()
