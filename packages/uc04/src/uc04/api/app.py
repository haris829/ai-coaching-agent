"""HTTP surface.

Thin by design: resolve the principal, validate, delegate, map the outcome to a status code.
No business logic lives here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..composition import Container, build_container
from ..domain.errors import (
    AccessDenied,
    NotEnrolled,
    NotFound,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    SessionIdentityError,
)
from ..domain.models import CoachingResponse
from ..domain.enums import FollowUpAction
from .schemas import (
    AskQuestionRequest,
    CoachingResponseSchema,
    ErrorResponse,
    FollowUpRequest,
)

API_PREFIX = "/api/v1/lesson-coaching"

logger = logging.getLogger("uc04.api")


def _error(status: int, code: str, message: str, rejected: list[str] | None = None) -> JSONResponse:
    payload = ErrorResponse(error_code=code, message=message, rejected_fields=rejected or [])
    return JSONResponse(status_code=status, content=payload.model_dump())


def create_app(container: Container | None = None) -> FastAPI:
    app = FastAPI(title="UC-04 Lesson Coaching", version="0.1.0")
    app.state.container = container or build_container()

    def get_container(request: Request) -> Container:
        return request.app.state.container

    def get_principal(request: Request, box: Container = Depends(get_container)) -> str:
        # Server-side resolution. The request body is never consulted for identity.
        headers = {k.lower(): v for k, v in request.headers.items()}
        return box.current_user.resolve(headers)

    # ------------------------------------------------------------------ error handling

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> Response:
        rejected: list[str] = []
        for error in exc.errors():
            location = [str(part) for part in error.get("loc", []) if part != "body"]
            if location:
                rejected.append(".".join(location))
        # Unknown fields are rejected outright and named, so nothing a caller sends can vanish
        # silently. A client trying to switch off quiz protection sees exactly that.
        return _error(422, "invalid_request", "The request was rejected.", sorted(set(rejected)))

    @app.exception_handler(AccessDenied)
    async def _denied(_request: Request, _exc: AccessDenied) -> Response:
        return _error(403, "access_denied", "You do not have access to this resource.")

    @app.exception_handler(NotEnrolled)
    async def _not_enrolled(_request: Request, exc: NotEnrolled) -> Response:
        # Distinct code, and the attempt is logged server-side.
        logger.warning(
            "enrolment_refused", extra={"course_id": exc.course_id, "reason": exc.reason or "not_enrolled"}
        )
        return _error(403, "not_enrolled", "You are not enrolled on this course.")

    @app.exception_handler(SessionIdentityError)
    async def _session(_request: Request, _exc: SessionIdentityError) -> Response:
        return _error(400, "session_required", "A session identifier is required.")

    @app.exception_handler(NotFound)
    async def _missing(_request: Request, _exc: NotFound) -> Response:
        return _error(404, "not_found", "The requested resource does not exist.")

    @app.exception_handler(ProviderTimeout)
    async def _timeout(_request: Request, _exc: ProviderTimeout) -> Response:
        # Retryable, and returned inside the caller's budget.
        return _error(504, "upstream_timeout", "A dependency did not respond in time. Retry shortly.")

    @app.exception_handler(ProviderUnavailable)
    async def _unavailable(_request: Request, _exc: ProviderUnavailable) -> Response:
        return _error(503, "upstream_unavailable", "A dependency is unavailable. Retry shortly.")

    @app.exception_handler(ProviderInvalidResponse)
    async def _invalid(_request: Request, _exc: ProviderInvalidResponse) -> Response:
        return _error(502, "upstream_invalid", "A dependency returned an unusable response.")

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> Response:
        logger.exception("unhandled_error", exc_info=exc)
        # Nothing internal escapes: no exception text, no provider name, no stack trace.
        return _error(500, "internal_error", "The request could not be completed.")

    # ------------------------------------------------------------------------- routes

    @app.get("/api/v1/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": "uc04-lesson-coaching"}

    @app.post(f"{API_PREFIX}/questions", response_model=CoachingResponseSchema)
    def ask(
        body: AskQuestionRequest,
        box: Container = Depends(get_container),
        principal: str = Depends(get_principal),
    ) -> CoachingResponseSchema:
        result = box.service.ask(
            session_id=body.session_id,
            user_id=principal,
            course_id=body.course_id,
            lesson_id=body.lesson_id,
            question=body.question,
        )
        return _to_schema(result)

    @app.post(f"{API_PREFIX}/questions/{{interaction_id}}/follow-up", response_model=CoachingResponseSchema)
    def follow_up(
        interaction_id: str,
        body: FollowUpRequest,
        box: Container = Depends(get_container),
        principal: str = Depends(get_principal),
    ) -> CoachingResponseSchema:
        if body.action is FollowUpAction.EXPLAIN_DIFFERENTLY:
            result = box.service.explain_differently(interaction_id=interaction_id, user_id=principal)
        else:
            result = box.service.go_deeper(interaction_id=interaction_id, user_id=principal)
        return _to_schema(result)

    return app


def _to_schema(result: CoachingResponse) -> CoachingResponseSchema:
    return CoachingResponseSchema.model_validate(result.model_dump())
