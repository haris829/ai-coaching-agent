"""Uniform error envelope and exception translation.

Error responses expose a stable code and a safe message. They never contain
report contents, weak topics, provider names, upstream error text, internal
exception messages or stack traces.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from uc07.domain.errors import (
    ConfigurationError,
    EvidenceIntegrityError,
    InteractionSourceUnusable,
    ReportOwnershipError,
)
from uc07.observability import log_event
from uc07.ports.identity import IdentityUnresolved


class UnknownRequestFields(Exception):
    """The request carried input UC-07 does not accept (including any user id)."""

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        super().__init__("request contains unsupported fields")


def envelope(
    code: str, message: str, *, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return {"error": body}


def _json(
    status_code: int, code: str, message: str, *, details: dict[str, Any] | None = None
) -> JSONResponse:
    log_event("request_failed", http_status=status_code, error_code=code)
    return JSONResponse(
        status_code=status_code, content=envelope(code, message, details=details)
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(UnknownRequestFields)
    async def _unknown_fields(_: Request, exc: UnknownRequestFields) -> JSONResponse:
        return _json(
            status.HTTP_400_BAD_REQUEST,
            "invalid_request",
            "This endpoint accepts no request parameters or body.",
            details={"rejected_fields": exc.fields},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted(
            {
                str(part)
                for error in exc.errors()
                for part in error.get("loc", ())
                if isinstance(part, str) and part not in {"query", "body", "header"}
            }
        )
        return _json(
            status.HTTP_400_BAD_REQUEST,
            "invalid_request",
            "The request could not be accepted.",
            details={"rejected_fields": fields} if fields else None,
        )

    @app.exception_handler(IdentityUnresolved)
    async def _identity(_: Request, __: IdentityUnresolved) -> JSONResponse:
        return _json(
            status.HTTP_401_UNAUTHORIZED,
            "identity_unresolved",
            "No server-side learner identity could be resolved for this request.",
        )

    @app.exception_handler(ReportOwnershipError)
    async def _ownership(_: Request, __: ReportOwnershipError) -> JSONResponse:
        return _json(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "This report does not belong to the resolved learner.",
        )

    @app.exception_handler(InteractionSourceUnusable)
    async def _interactions(_: Request, exc: InteractionSourceUnusable) -> JSONResponse:
        return _json(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "interaction_source_unusable",
            "Coaching interaction history could not be loaded, so no knowledge-gap "
            "report can be produced. This is a source failure, not an empty history.",
            details={"interaction_source_status": exc.source_status},
        )

    @app.exception_handler(EvidenceIntegrityError)
    async def _evidence(_: Request, __: EvidenceIntegrityError) -> JSONResponse:
        return _json(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "The request could not be completed.",
        )

    @app.exception_handler(ConfigurationError)
    async def _configuration(_: Request, __: ConfigurationError) -> JSONResponse:
        return _json(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "The request could not be completed.",
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, __: Exception) -> JSONResponse:
        # No exception text, no stack trace, no internals.
        return _json(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "The request could not be completed.",
        )
