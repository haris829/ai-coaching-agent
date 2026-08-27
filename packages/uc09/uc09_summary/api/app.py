"""FastAPI application and routes.

Every route resolves the caller server-side and checks ownership. A session
identifier is never sufficient on its own to obtain a summary: the record is
fetched, its owner is compared to the resolved caller, and a mismatch is
answered exactly as a missing record is, so that a probe learns nothing.

Error responses carry a code and a fixed message. Upstream error text, provider
names, stack detail and summary content never appear in a response body.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

from uc09_summary import __version__
from uc09_summary.api.schemas import (
    DownloadEventOut,
    ErrorBody,
    ErrorResponse,
    GenerateSummaryRequest,
    HealthResponse,
    SummaryResponse,
)
from uc09_summary.application.summary_service import SummaryService
from uc09_summary.composition import PORTS, Container, build_container
from uc09_summary.config import Settings
from uc09_summary.domain.errors import (
    AccessDenied,
    IdentityUnresolved,
    ProviderError,
    SessionNotFound,
    SummaryNotFound,
)
from uc09_summary.logging_setup import get_logger

_log = get_logger(__name__)

#: Starlette renamed this constant; support both without a deprecation warning.
HTTP_422_UNPROCESSABLE = getattr(
    status, "HTTP_422_UNPROCESSABLE_CONTENT", 422
)

#: Fixed client-facing messages. Never interpolated with internal detail.
_MESSAGES = {
    "summary_not_found": "No such summary is available to you.",
    "session_not_found": "No such session is available to you.",
    "identity_unresolved": "Caller identity could not be resolved.",
    "upstream_unavailable": "The session record could not be read. Try again shortly.",
    "invalid_request": "The request body was not accepted.",
    "dev_minting_disabled": (
        "Session minting is disabled. This component receives a session "
        "identifier and does not create one."
    ),
}


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Args:
        container: a pre-wired container. Built from settings when omitted.
        settings: settings used to build the container.

    Returns:
        The configured FastAPI application.
    """
    container = container or build_container(settings)

    app = FastAPI(
        title="UC-09 Session Summary and Export",
        version=__version__,
        description=(
            "Turns a recorded coaching session into a structured summary and "
            "exports it as CPD evidence. Read-only with respect to every "
            "upstream system."
        ),
    )
    app.state.container = container

    _register_error_handlers(app)
    _register_routes(app)
    if container.settings.allow_dev_session_minting:
        _register_dev_routes(app)
    return app


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def get_service(container: Container = Depends(get_container)) -> SummaryService:
    return container.service


def get_current_user(
    request: Request, container: Container = Depends(get_container)
) -> str:
    """Resolve the caller server-side. Never taken from a request body or query."""
    return container.current_user_provider.resolve(request)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


def _register_routes(app: FastAPI) -> None:
    @app.post(
        "/api/v1/sessions/{session_id}/summary",
        response_model=SummaryResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Generate or regenerate the summary of a session",
    )
    def generate_summary(
        session_id: str,
        body: GenerateSummaryRequest | None = None,
        service: SummaryService = Depends(get_service),
        user_id: str = Depends(get_current_user),
    ) -> SummaryResponse:
        record = service.generate(session_id, user_id)
        return SummaryResponse.from_record(record)

    @app.get(
        "/api/v1/summaries/{summary_id}",
        response_model=SummaryResponse,
        summary="Retrieve a structured summary",
    )
    def get_summary(
        summary_id: str,
        service: SummaryService = Depends(get_service),
        user_id: str = Depends(get_current_user),
    ) -> SummaryResponse:
        return SummaryResponse.from_record(service.get(summary_id, user_id))

    @app.get(
        "/api/v1/summaries/{summary_id}/preview",
        response_class=HTMLResponse,
        summary="Printable HTML preview, available before download",
    )
    def preview_summary(
        summary_id: str,
        service: SummaryService = Depends(get_service),
        user_id: str = Depends(get_current_user),
    ) -> HTMLResponse:
        record, html = service.preview_html(summary_id, user_id)
        return HTMLResponse(
            content=html,
            headers={
                "X-Summary-Id": record.summary_id,
                "X-Summary-Is-Partial": str(record.is_partial).lower(),
                "X-Summary-Generation-Mode": record.generation_mode.value,
            },
        )

    @app.get(
        "/api/v1/summaries/{summary_id}/pdf",
        summary="Download the CPD evidence PDF. Logs the download.",
        responses={
            200: {
                "content": {"application/pdf": {}, "text/html": {}},
                "description": (
                    "The PDF, or the printable HTML document when PDF "
                    "rendering was unavailable."
                ),
            }
        },
    )
    def download_pdf(
        summary_id: str,
        service: SummaryService = Depends(get_service),
        user_id: str = Depends(get_current_user),
    ) -> Response:
        result = service.export(summary_id, user_id)
        headers = {
            "X-Summary-Id": result.summary.summary_id,
            "X-Session-Id": result.summary.session_id,
            "X-Summary-Is-Partial": str(result.summary.is_partial).lower(),
            "X-Pdf-Available": str(result.pdf_available).lower(),
        }
        if result.pdf_available and result.pdf is not None:
            headers["Content-Disposition"] = (
                f'attachment; filename="cpd-evidence-{result.summary.summary_id}.pdf"'
            )
            return Response(
                content=result.pdf, media_type="application/pdf", headers=headers
            )
        # Never block the learner from their session record.
        headers["X-Pdf-Unavailable-Reason"] = "renderer_failed"
        return HTMLResponse(content=result.html, headers=headers)

    @app.get(
        "/api/v1/summaries/{summary_id}/downloads",
        response_model=list[DownloadEventOut],
        summary="Download events recorded for a summary",
    )
    def list_downloads(
        summary_id: str,
        container: Container = Depends(get_container),
        service: SummaryService = Depends(get_service),
        user_id: str = Depends(get_current_user),
    ) -> list[DownloadEventOut]:
        record = service.get(summary_id, user_id)
        events = container.providers["download_log_repository"].for_summary(
            record.summary_id
        )
        return [
            DownloadEventOut(
                download_id=e.download_id,
                summary_id=e.summary_id,
                session_id=e.session_id,
                downloaded_at=e.downloaded_at,
                format=e.format,
                pdf_available=e.pdf_available,
                byte_count=e.byte_count,
            )
            for e in events
        ]

    @app.get("/api/v1/healthz", response_model=HealthResponse, summary="Liveness")
    def healthz(container: Container = Depends(get_container)) -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            providers={port: getattr(container.settings, port) for port in PORTS},
        )


def _register_dev_routes(app: FastAPI) -> None:
    """Dev-only session minting. Registered only when the flag is on.

    This component receives an opaque ``session_id`` on every production path.
    The route exists so a developer can exercise the API without a session
    service, and it is absent from the application entirely unless
    ``UC09_ALLOW_DEV_SESSION_MINTING=true``.
    """
    import uuid

    @app.post(
        "/api/v1/dev/sessions",
        status_code=status.HTTP_201_CREATED,
        summary="DEV ONLY: mint a session identifier",
    )
    def mint_session() -> dict[str, str]:
        _log.warning("dev_session_minted", reason="allow_dev_session_minting_enabled")
        return {"session_id": f"dev-sess-{uuid.uuid4().hex[:12]}"}


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


def _error(code: str, http_status: int) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=_MESSAGES[code]))
    return JSONResponse(status_code=http_status, content=body.model_dump())


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SummaryNotFound)
    def _summary_not_found(request: Request, exc: SummaryNotFound) -> JSONResponse:
        return _error("summary_not_found", status.HTTP_404_NOT_FOUND)

    @app.exception_handler(AccessDenied)
    def _access_denied(request: Request, exc: AccessDenied) -> JSONResponse:
        # Answered as not-found so that ownership cannot be probed. The denial
        # is recorded in the application log, where it belongs.
        return _error("summary_not_found", status.HTTP_404_NOT_FOUND)

    @app.exception_handler(SessionNotFound)
    def _session_not_found(request: Request, exc: SessionNotFound) -> JSONResponse:
        return _error("session_not_found", status.HTTP_404_NOT_FOUND)

    @app.exception_handler(IdentityUnresolved)
    def _identity_unresolved(request: Request, exc: IdentityUnresolved) -> JSONResponse:
        return _error("identity_unresolved", status.HTTP_401_UNAUTHORIZED)

    @app.exception_handler(ProviderError)
    def _provider_error(request: Request, exc: ProviderError) -> JSONResponse:
        # exc.detail may carry upstream text. It is logged, never returned.
        _log.error("request_failed_upstream", error_code=exc.code, port=exc.port)
        return _error("upstream_unavailable", status.HTTP_503_SERVICE_UNAVAILABLE)

    @app.exception_handler(RequestValidationError)
    def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Unknown fields are rejected outright. The response says the body was
        # not accepted and nothing further: a validation echo is a disclosure
        # channel and this endpoint has nothing to gain from one.
        _log.info("request_rejected", reason="schema_validation")
        return _error("invalid_request", HTTP_422_UNPROCESSABLE)


def build_default_app() -> Any:
    """Entry point for ``uvicorn uc09_summary.api.app:build_default_app --factory``."""
    return create_app()
