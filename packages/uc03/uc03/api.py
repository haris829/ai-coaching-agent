"""HTTP surface for UC-03.

Backend only. The response body carries everything a future frontend needs to
render the four parts as separate sections, drive the thinking state, and offer
retry - but no rendering happens here.

Security posture:
  * The request body forbids unknown fields, so a client that tries to send
    `naric_level`, `practice_area`, `user_id` or `system_prompt` is rejected
    with 422 rather than having the value silently ignored.
  * Identity comes from the Authorization header only.
  * Session ownership is verified server-side inside the service.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import settings
from .domain.models import Principal, QuestionResponse
from .domain.enums import FollowUpAction
from .errors import (
    AuthenticationError,
    AuthorizationError,
    InputValidationError,
    InteractionNotFoundError,
)
from .factory import build_default_service
from .service import QAService


class AskRequest(BaseModel):
    """The complete set of fields a client may send.

    `extra="forbid"` is load-bearing: it is what makes an attempt to override
    NARIC level, practice area, identity or prompts a hard 422.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=settings.min_question_chars,
        max_length=settings.max_question_chars,
    )
    session_id: str = Field(min_length=1, max_length=128)


class FollowUpRequest(BaseModel):
    """Body for a follow-up action. Same closed-field posture as AskRequest."""

    model_config = ConfigDict(extra="forbid")

    action: FollowUpAction
    session_id: str = Field(min_length=1, max_length=128)


def create_app(service: QAService | None = None) -> FastAPI:
    app = FastAPI(
        title="UC-03 Legal Concept Q&A",
        version="1.0.0",
        description=(
            "Standalone backend for UC-03. UC-01 and UC-02 are consumed through "
            "the ContextProvider contract and are mocked here."
        ),
    )
    app.state.service = service or build_default_service()

    def get_service(request: Request) -> QAService:
        return request.app.state.service

    async def get_principal(
        authorization: str | None = Header(default=None),
        svc: QAService = Depends(get_service),
    ) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer credential.",
            )
        token = authorization.split(" ", 1)[1].strip()
        try:
            return await svc.authenticate(token)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/uc03/questions",
        response_model=QuestionResponse,
        response_model_exclude_none=False,
    )
    async def ask(
        body: AskRequest,
        principal: Principal = Depends(get_principal),
        svc: QAService = Depends(get_service),
    ) -> QuestionResponse:
        try:
            return await svc.answer(
                question=body.question,
                session_id=body.session_id,
                principal=principal,
            )
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except InputValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @app.post(
        "/uc03/questions/{question_id}/follow-up",
        response_model=QuestionResponse,
        response_model_exclude_none=False,
    )
    async def follow_up(
        question_id: str,
        body: FollowUpRequest,
        principal: Principal = Depends(get_principal),
        svc: QAService = Depends(get_service),
    ) -> QuestionResponse:
        """Perform a real follow-up: re-explain with an unused framing.

        Returns 404 when the interaction does not exist or belongs to another
        caller - the two are deliberately indistinguishable.
        """
        try:
            return await svc.follow_up(
                question_id=question_id,
                action=body.action,
                session_id=body.session_id,
                principal=principal,
            )
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except InteractionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        except InputValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @app.post("/uc03/questions/stream")
    async def ask_stream(
        body: AskRequest,
        principal: Principal = Depends(get_principal),
        svc: QAService = Depends(get_service),
    ) -> StreamingResponse:
        """Server-sent events carrying the thinking signal then the result.

        This exists so the future frontend can render its thinking animation
        from a real server signal at the 1.5s mark rather than guessing. The
        animation itself is not built here.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def on_thinking() -> None:
            await queue.put(
                json.dumps(
                    {"event": "thinking", "after_ms": svc.settings.thinking_after_ms}
                )
            )

        async def run() -> None:
            try:
                response = await svc.answer(
                    question=body.question,
                    session_id=body.session_id,
                    principal=principal,
                    on_thinking=on_thinking,
                )
                payload = {"event": "result", "data": response.model_dump(mode="json")}
            except AuthorizationError as exc:
                payload = {"event": "error", "detail": str(exc), "status": 403}
            except InputValidationError as exc:
                payload = {"event": "error", "detail": str(exc), "status": 422}
            await queue.put(json.dumps(payload))
            await queue.put(None)

        async def events():
            task = asyncio.create_task(run())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield f"data: {item}\n\n"
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


app = create_app()
