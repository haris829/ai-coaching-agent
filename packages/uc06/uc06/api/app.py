"""FastAPI application.

Three endpoints, one error envelope, and one rule: nothing leaves the
case-coaching surface except through ResponseEmitter.emit(), which runs the
disclaimer boundary check first.

What never reaches a client: internal exception text, stack traces, provider
names, prompt content, system instructions, generator configuration, or case
content the caller cannot already access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..composition import Container, assert_registry_complete, build_container
from ..config import Settings
from ..domain.enums import SecurityIncidentKind
from ..domain.errors import ProviderUnavailable
from ..domain.guard_vocabulary import SUPPRESSION_FIELD_NAMES
from ..domain.models import SecurityIncident
from ..domain.responses import DisclaimedResponse, SafeErrorResponse
from ..logging_setup import configure_logging, get_logger
from .schemas import AskCaseQuestionRequest

_log = get_logger("api")

API_PREFIX = "/api/v1/case-coaching"
DEV_SESSION_PREFIX = "dev-session-"


@dataclass(frozen=True)
class _StatusResponse(DisclaimedResponse):
    """Halt state for a caller to render.

    Inherits the disclaimer like every other response type: it cannot be
    constructed without it, and it goes through the same boundary check.
    """

    body: dict[str, Any]

    def _body(self) -> dict[str, Any]:
        return dict(self.body)


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    configure_logging()
    assert_registry_complete()
    # Eager resolution: a misconfigured provider fails here, at startup, naming
    # the missing implementation - never silently at request time on a mock.
    box = container or build_container(settings)

    app = FastAPI(
        title="UC-06 Case-Linked Legal Advice Coaching",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.container = box

    def get_container() -> Container:
        return app.state.container

    # ---------------------------------------------------------------- errors
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Rejected fields are named; submitted values are never echoed back.

        A rejected field whose name would influence a control UC-06 owns is also
        recorded as a security incident.
        """
        rejected = sorted({_field_name(err) for err in exc.errors() if _field_name(err)})
        suppression = tuple(f for f in rejected if f.lower() in SUPPRESSION_FIELD_NAMES)
        if suppression:
            box_ = get_container()
            box_.security_incidents.record(
                SecurityIncident(
                    incident_id=uuid4().hex,
                    occurred_at=datetime.now(timezone.utc),
                    kind=SecurityIncidentKind.REQUEST_FIELD_SUPPRESSION,
                    session_id=None,
                    user_id=None,
                    case_file_id=None,
                    matched_rule_ids=tuple(suppression),
                    detail_code="suppression_field_in_request",
                )
            )
        request_id = _request_id(request)
        _log.warning(
            "case_coaching.requested",
            request_id=request_id,
            status_code=422,
            path=request.url.path,
            error_code="invalid_request",
            rejected_fields=rejected,
        )
        payload = SafeErrorResponse(
            code="invalid_request",
            message=(
                "The request was rejected. Unrecognised or invalid fields: "
                + (", ".join(rejected) if rejected else "request body")
                + ". Fields controlled by the service cannot be supplied by a client."
            ),
            request_id=request_id,
        ).to_payload()
        return JSONResponse(status_code=422, content=payload)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """Last resort. Logs the exception TYPE only - internal exception text can
        carry case content and never reaches a log or a client."""
        request_id = _request_id(request)
        _log.error(
            "case_coaching.generation_failed",
            request_id=request_id,
            path=request.url.path,
            status_code=500,
            error_code="internal_error",
            kind=type(exc).__name__,
        )
        payload = SafeErrorResponse(
            code="internal_error",
            message="The request could not be completed.",
            request_id=request_id,
        ).to_payload()
        return JSONResponse(status_code=500, content=payload)

    # ------------------------------------------------------------- endpoints
    @app.post(API_PREFIX + "/questions")
    async def ask_question(
        payload: AskCaseQuestionRequest,
        request: Request,
        box: Container = Depends(get_container),
    ) -> JSONResponse:
        request_id = _request_id(request)

        try:
            user_id = box.current_user.resolve(request.headers)
        except ProviderUnavailable:
            body = SafeErrorResponse(
                code="identity_unavailable",
                message="The request could not be attributed to a signed-in user.",
                request_id=request_id,
            ).to_payload()
            return JSONResponse(status_code=401, content=body)

        session_id = payload.session_id
        if not session_id:
            if not box.settings.allow_dev_session_ids:
                body = SafeErrorResponse(
                    code="session_id_required",
                    message="A session identifier is required. This service does not create sessions.",
                    request_id=request_id,
                ).to_payload()
                return JSONResponse(status_code=400, content=body)
            session_id = DEV_SESSION_PREFIX + uuid4().hex

        outcome = box.service.ask(
            session_id=session_id,
            user_id=user_id,
            question=payload.question,
            case_file_id=payload.case_file_id,
            request_id=request_id,
        )
        body, status = box.emitter.emit(
            outcome.response,
            session_id=outcome.session_id,
            user_id=user_id,
            case_file_id=outcome.case_file_id,
            request_id=request_id,
            status_code=outcome.status_code,
        )
        return JSONResponse(status_code=status, content=body)

    @app.get(API_PREFIX + "/sessions/{session_id}/status")
    async def session_status(
        session_id: str,
        request: Request,
        box: Container = Depends(get_container),
    ) -> JSONResponse:
        request_id = _request_id(request)
        try:
            user_id = box.current_user.resolve(request.headers)
        except ProviderUnavailable:
            body = SafeErrorResponse(
                code="identity_unavailable",
                message="The request could not be attributed to a signed-in user.",
                request_id=request_id,
            ).to_payload()
            return JSONResponse(status_code=401, content=body)

        result, status = box.service.session_status(session_id, user_id)
        if status != 200:
            body = SafeErrorResponse(
                code="session_not_visible",
                message="This session is not visible to you.",
                request_id=request_id,
            ).to_payload()
            return JSONResponse(status_code=403, content=body)

        # Status is a response from the case-coaching surface, so it carries the
        # disclaimer like every other response from it.
        checked, code = box.emitter.emit(
            _StatusResponse(result),
            session_id=session_id,
            user_id=user_id,
            case_file_id=None,
            request_id=request_id,
            status_code=200,
        )
        return JSONResponse(status_code=code, content=checked)

    @app.get("/api/v1/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(status_code=200, content={"status": "ok"})

    return app


def _request_id(request: Request) -> str:
    header = request.headers.get("x-request-id")
    return header.strip() if header and header.strip() else uuid4().hex


def _field_name(error: dict[str, Any]) -> str:
    location = error.get("loc") or ()
    parts = [str(p) for p in location if p != "body"]
    return ".".join(parts)


app = create_app()
