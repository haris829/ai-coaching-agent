"""HTTP error shaping.

Error responses are content-free by construction: they carry a machine-readable code, a
fixed human message and a retry hint.  No rating, comment, question or response text can
appear in one, including in a validation error -- pydantic's echo of the offending input
is stripped before the response is built.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from uc10.logging_setup import get_logger

log = get_logger("uc10.api")

# Starlette renamed this constant; support both spellings without a deprecation warning.
UNPROCESSABLE = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


def api_error(
    status_code: int, code: str, message: str, *, retryable: bool = False
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "retryable": retryable},
    )


async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail: Any = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        body = {"error": detail}
    else:
        body = {
            "error": {
                "code": "http_error",
                "message": str(detail) if isinstance(detail, str) else "Request failed.",
                "retryable": False,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Report *where* a request was invalid, never *what* it contained.

    ``input`` and ``ctx`` are dropped: on a too-long comment they would echo the
    learner's free text straight back into the response and into any access log.
    """
    fields = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        fields.append({"field": location or "body", "issue": str(error.get("type", "invalid"))})
    log.info("request_validation_failed", field_count=len(fields))
    return JSONResponse(
        status_code=UNPROCESSABLE,
        content={
            "error": {
                "code": "validation_error",
                "message": "The request could not be accepted.",
                "retryable": False,
                "fields": fields,
            }
        },
    )
