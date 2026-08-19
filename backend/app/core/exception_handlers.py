"""Global exception handling — one envelope for the whole API.

Guarantees:

* every error response uses the same envelope — see :mod:`app.core.errors`;
* stack traces and driver messages are logged, never returned;
* an unexpected exception becomes a generic 500 rather than leaking internals;
* a database fault does not take the process down;
* every response carries the ``requestId`` that appears in the server log line, so a report of
  "it failed at 14:32" can be traced to the exact request.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.time import to_iso, utcnow

logger = get_logger(__name__)


def request_id_of(request: Request) -> str | None:
    """The correlation id the request middleware attached, when there is one."""
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _envelope(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    details: list[dict[str, str]] | None = None,
    context: dict[str, object] | None = None,
    retryable: bool = False,
) -> dict[str, object]:
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "requestId": request_id,
        "timestamp": to_iso(utcnow()),
    }
    if details:
        error["details"] = details
    if context:
        error["context"] = context
    return {"error": error}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        log = logger.warning if exc.status_code < 500 else logger.error
        log(
            "request.failed",
            extra={
                "path": request.url.path,
                "method": request.method,
                "status": exc.status_code,
                "code": exc.code,
                "request_id": request_id_of(request),
                "detail_count": len(exc.details),
                **exc.log_context,
            },
            exc_info=exc.status_code >= 500,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response(request_id=request_id_of(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Malformed request body/params — a client contract error, not a domain error."""
        details = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body")
                or "body",
                "code": str(error.get("type", "INVALID_REQUEST")).upper(),
                "message": str(error.get("msg", "Invalid value.")),
            }
            for error in exc.errors()
        ]
        logger.warning(
            "request.malformed",
            extra={
                "path": request.url.path,
                "method": request.method,
                "issues": len(details),
                "request_id": request_id_of(request),
            },
        )
        return JSONResponse(
            status_code=400,
            content=_envelope(
                "BAD_REQUEST",
                "The request could not be processed because it is malformed.",
                request_id=request_id_of(request),
                details=details,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            413: "PAYLOAD_TOO_LARGE",
            415: "UNSUPPORTED_MEDIA_TYPE",
        }.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                code,
                str(exc.detail) if exc.detail else "Request failed.",
                request_id=request_id_of(request),
            ),
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        # The driver message can contain column values, so it is logged and not returned.
        logger.error(
            "database.integrity_error",
            extra={
                "path": request.url.path,
                "method": request.method,
                "request_id": request_id_of(request),
            },
            exc_info=exc,
        )
        return JSONResponse(
            status_code=409,
            content=_envelope(
                "INTEGRITY_CONFLICT",
                "The request conflicts with existing data and was not applied.",
                request_id=request_id_of(request),
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error(
            "database.error",
            extra={
                "path": request.url.path,
                "method": request.method,
                "request_id": request_id_of(request),
            },
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content=_envelope(
                "DATABASE_ERROR",
                "A database error prevented the operation from completing.",
                request_id=request_id_of(request),
                # A transient lock or connection fault is worth retrying; the caller cannot tell
                # which it was, and a failed read is safe to repeat.
                retryable=True,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "request.unhandled_exception",
            extra={
                "path": request.url.path,
                "method": request.method,
                "request_id": request_id_of(request),
            },
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content=_envelope(
                "INTERNAL_ERROR",
                "An unexpected internal error occurred.",
                request_id=request_id_of(request),
            ),
        )
