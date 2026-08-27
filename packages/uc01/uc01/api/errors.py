"""HTTP error handling.

Every failure leaves the API as the same safe envelope:

    {"error": {"code": "...", "message": "..."}, "recovery": {...}?}

The user never sees a traceback, an exception class, a SQL statement, an upstream error
message, a URL or a key. The technical detail is logged server-side with the same
request in view.

``debug`` is added only when ``UC01_EXPOSE_ERROR_DETAILS=true`` **and** dev mode is on —
the "developer-only mode" escape hatch, off by default.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..domain.errors import (
    AuthenticationRequiredError,
    DependencyDegradedError,
    ModeUnavailableError,
    SelectionNotAccessibleError,
    SelectionNotAllowedError,
    SelectionRequiredError,
    SessionInitializationError,
    SessionNotFoundError,
    Uc01Error,
)
from .schemas import ErrorBody, ErrorResponse, RecoveryOut

logger = logging.getLogger(__name__)

HTTP_422_UNPROCESSABLE = 422
"""Literal rather than ``status.HTTP_422_*``: the constant was renamed between Starlette
versions and the old name emits a deprecation warning."""

GENERIC_MESSAGE = "Something went wrong on our side. Please try again."

_STATUS_BY_ERROR: Mapping[type, int] = {
    AuthenticationRequiredError: status.HTTP_401_UNAUTHORIZED,
    SelectionRequiredError: status.HTTP_400_BAD_REQUEST,
    SelectionNotAllowedError: status.HTTP_400_BAD_REQUEST,
    SelectionNotAccessibleError: status.HTTP_403_FORBIDDEN,
    SessionNotFoundError: status.HTTP_404_NOT_FOUND,
    ModeUnavailableError: status.HTTP_409_CONFLICT,
    DependencyDegradedError: status.HTTP_503_SERVICE_UNAVAILABLE,
    SessionInitializationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def http_status_for(error: Uc01Error) -> int:
    for error_type, code in _STATUS_BY_ERROR.items():
        if isinstance(error, error_type):
            return code
    return status.HTTP_400_BAD_REQUEST


def _recovery_for(error: Uc01Error) -> RecoveryOut | None:
    context = error.context or {}
    if not any(key in context for key in ("available_modes", "session_id", "suggested_mode")):
        return None
    return RecoveryOut(
        session_id=context.get("session_id"),  # type: ignore[arg-type]
        available_modes=list(context.get("available_modes") or []),  # type: ignore[arg-type]
        suggested_mode=context.get("suggested_mode"),  # type: ignore[arg-type]
    )


def _payload(
    *,
    code: str,
    message: str,
    recovery: RecoveryOut | None = None,
    fields: list[Mapping[str, Any]] | None = None,
    debug: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message),
        recovery=recovery,
        fields=fields,
        debug=debug,
    )
    return body.model_dump(exclude_none=True)


def register_error_handlers(app: FastAPI) -> None:
    """Install the exception handlers on the app."""

    @app.exception_handler(Uc01Error)
    async def _handle_uc01_error(request: Request, exc: Uc01Error) -> JSONResponse:
        http_status = http_status_for(exc)
        settings = request.app.state.container.settings
        log = logger.warning if http_status < 500 else logger.error
        log(
            "api.request_failed",
            extra={
                "uc01": {
                    "path": request.url.path,
                    "method": request.method,
                    "status": http_status,
                    "code": exc.failure_code,
                    "technical_detail": exc.technical_detail,
                    "context": dict(exc.context),
                }
            },
        )
        debug = (
            {"technical_detail": exc.technical_detail, "context": dict(exc.context)}
            if settings.expose_error_details
            else None
        )
        return JSONResponse(
            status_code=http_status,
            content=_payload(
                code=exc.code,
                message=exc.user_message,
                recovery=_recovery_for(exc),
                debug=debug,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Rejected input, including attempts to send fields the client does not own.

        Field errors are echoed because they describe the caller's own request, but the
        raw Pydantic error objects (which can contain arbitrary input values) are
        reduced to location + type + message.
        """
        fields = [
            {
                "location": ".".join(str(part) for part in error.get("loc", ())),
                "type": error.get("type", "invalid"),
                "message": error.get("msg", "Invalid value."),
            }
            for error in exc.errors()
        ]
        logger.info(
            "api.request_invalid",
            extra={"uc01": {"path": request.url.path, "fields": fields}},
        )
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE,
            content=_payload(
                code="invalid_request",
                message="Some of the values sent with this request are not valid.",
                fields=fields,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        message = (
            "That resource could not be found."
            if exc.status_code == status.HTTP_404_NOT_FOUND
            else GENERIC_MESSAGE
            if exc.status_code >= 500
            else str(exc.detail)
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(code=f"http_{exc.status_code}", message=message),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort. The traceback is logged; the client gets a safe message.

        This is not a silent ``except Exception: pass`` — nothing is swallowed, the
        error is logged with its stack trace and the response carries a stable code.
        """
        settings = request.app.state.container.settings
        logger.exception(
            "api.unhandled_exception",
            extra={"uc01": {"path": request.url.path, "method": request.method}},
        )
        debug = (
            {"exception": type(exc).__name__, "technical_detail": str(exc)}
            if settings.expose_error_details
            else None
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload(code="internal_error", message=GENERIC_MESSAGE, debug=debug),
        )


__all__ = ["GENERIC_MESSAGE", "http_status_for", "register_error_handlers"]
