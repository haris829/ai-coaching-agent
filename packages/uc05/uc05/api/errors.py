"""Uniform error envelope.

No internal exception text, provider name, prompt content or stack trace
reaches a client.  The mapping is from error *class* to a fixed code and a
fixed, generic message; the detail an adapter attached to a ``ProviderError``
is logged and dropped here, not forwarded.

``request_id`` is what a learner quotes to support and what an operator greps
for.  It is the only bridge between the safe outside message and the detailed
inside log line.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ..application.logging_config import log_event
from ..domain.errors import (
    AccessDenied,
    DevEndpointDisabled,
    DialogueNotFound,
    InvalidTransition,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    UC05Error,
)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool
    request_id: str


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


#: error class -> (status, code, safe message, retryable)
_MAPPING: tuple[tuple[type[Exception], int, str, str, bool], ...] = (
    (
        AccessDenied,
        403,
        "forbidden",
        "This resource does not belong to the current user.",
        False,
    ),
    (
        DialogueNotFound,
        404,
        "not_found",
        "No such dialogue.",
        False,
    ),
    (
        DevEndpointDisabled,
        404,
        "not_found",
        "No such endpoint.",
        False,
    ),
    (
        InvalidTransition,
        409,
        "invalid_state",
        "This dialogue is closed, or that action is not available in its "
        "current state.",
        False,
    ),
    (
        ProviderTimeout,
        504,
        "upstream_timeout",
        "The request could not be completed in time. Nothing was recorded; "
        "please try again.",
        True,
    ),
    (
        ProviderUnavailable,
        503,
        "upstream_unavailable",
        "A required service is unavailable. Nothing was recorded; please try "
        "again.",
        True,
    ),
    (
        ProviderInvalidResponse,
        502,
        "upstream_invalid_response",
        "The response could not be produced correctly and was rejected rather "
        "than shown. Nothing was recorded.",
        False,
    ),
)


def _envelope(
    status: int, code: str, message: str, retryable: bool, request_id: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorEnvelope(
            error=ErrorBody(
                code=code,
                message=message,
                retryable=retryable,
                request_id=request_id,
            )
        ).model_dump(),
    )


async def uc05_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid.uuid4())
    for error_type, status, code, message, retryable in _MAPPING:
        if isinstance(exc, error_type):
            log_event(
                "request.failed",
                level=logging.WARNING,
                error_type=type(exc).__name__,
                port=getattr(exc, "port", None),
                retryable=retryable,
                outcome=code,
                interaction_id=request_id,
            )
            return _envelope(status, code, message, retryable, request_id)

    log_event(
        "request.failed",
        level=logging.ERROR,
        error_type=type(exc).__name__,
        outcome="internal_error",
        interaction_id=request_id,
    )
    return _envelope(
        500,
        "internal_error",
        "Something went wrong. Nothing was recorded.",
        False,
        request_id,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Reject unknown fields visibly.

    Request schemas set ``extra="forbid"``, so an attempt to send
    ``naric_level``, ``response_kind``, ``resolution`` or ``system_prompt``
    produces this, not a silent ignore.  The field names are echoed because
    they came from the caller; nothing internal is added.
    """
    request_id = str(uuid.uuid4())
    offending = sorted(
        {
            str(error.get("loc", ("body",))[-1])
            for error in exc.errors()
            if error.get("type") == "extra_forbidden"
        }
    )
    if offending:
        message = (
            "Unknown field(s) rejected: "
            + ", ".join(offending)
            + ". These values are set server-side and cannot be supplied by a client."
        )
    else:
        message = "The request body did not match the expected schema."

    log_event(
        "request.rejected",
        level=logging.INFO,
        outcome="validation_error",
        interaction_id=request_id,
        count=len(exc.errors()),
    )
    return _envelope(422, "validation_error", message, False, request_id)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, UC05Error):
        return await uc05_error_handler(request, exc)
    return await uc05_error_handler(request, exc)
