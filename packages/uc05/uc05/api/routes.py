"""HTTP routes.

Thin by design: resolve identity, call the service, project the result.  No
rule lives here -- if a behaviour can only be found by reading this file,
it is in the wrong place.

There is no frontend.  ``GET /mode/{session_id}`` exposes the state a mode
indicator needs and stops there; the toolbar toggle is presentation and is not
UC-05's to build.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from ..application.socratic_service import SocraticService, mint_dev_session_id
from ..composition import Container, get_container
from ..domain.errors import ProviderUnavailable
from .schemas import (
    DialogueExchangeView,
    DialogueMessageView,
    DialogueView,
    ExchangeProgress,
    HealthResponse,
    ModeResponse,
    ReplyRequest,
    SetModeRequest,
    SocraticResponse,
    StartQuestionRequest,
)

router = APIRouter(prefix="/api/v1")


def container_dep() -> Container:
    return get_container()


def service_dep(container: Container = Depends(container_dep)) -> SocraticService:
    return container.service


async def current_user_dep(
    request: Request, container: Container = Depends(container_dep)
) -> str:
    """Resolve the acting user server-side.

    ``user_id`` never appears in a request body.  A failure to establish
    identity is an authentication problem, not an upstream outage, so it is
    translated here rather than travelling as a ``ProviderUnavailable``.
    """
    from fastapi import HTTPException

    try:
        return await container.current_user.resolve(request)
    except ProviderUnavailable:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthenticated",
        ) from None


# --------------------------------------------------------------------------
# Mode
# --------------------------------------------------------------------------


@router.get("/socratic/mode/{session_id}", response_model=ModeResponse)
async def get_mode(
    session_id: str,
    user_id: str = Depends(current_user_dep),
    service: SocraticService = Depends(service_dep),
) -> ModeResponse:
    return ModeResponse.of(await service.get_mode(session_id, user_id))


@router.put("/socratic/mode/{session_id}", response_model=ModeResponse)
async def set_mode(
    session_id: str,
    body: SetModeRequest,
    user_id: str = Depends(current_user_dep),
    service: SocraticService = Depends(service_dep),
) -> ModeResponse:
    return ModeResponse.of(await service.set_mode(session_id, user_id, body.enabled))


# --------------------------------------------------------------------------
# Dialogue
# --------------------------------------------------------------------------


@router.post(
    "/socratic/questions",
    response_model=SocraticResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_question(
    body: StartQuestionRequest,
    user_id: str = Depends(current_user_dep),
    service: SocraticService = Depends(service_dep),
) -> SocraticResponse:
    turn = await service.ask(
        session_id=body.session_id,
        user_id=user_id,
        question_text=body.question_text,
        topic_tag=body.topic_tag,
    )
    return SocraticResponse.of(turn)


@router.post("/socratic/dialogues/{dialogue_id}/reply", response_model=SocraticResponse)
async def reply(
    dialogue_id: str,
    body: ReplyRequest,
    user_id: str = Depends(current_user_dep),
    service: SocraticService = Depends(service_dep),
) -> SocraticResponse:
    turn = await service.reply(
        dialogue_id=dialogue_id, user_id=user_id, message=body.message
    )
    return SocraticResponse.of(turn)


@router.get("/socratic/dialogues/{dialogue_id}", response_model=DialogueView)
async def get_dialogue(
    dialogue_id: str,
    user_id: str = Depends(current_user_dep),
    service: SocraticService = Depends(service_dep),
) -> DialogueView:
    """The owner's own dialogue.  Ownership is checked in the service."""
    dialogue = await service.get_dialogue(dialogue_id, user_id)
    return DialogueView(
        dialogue_id=dialogue.dialogue_id,
        session_id=dialogue.session_id,
        question_text=dialogue.question_text,
        topic_tag=dialogue.topic_tag,
        state=dialogue.state,
        resolution=dialogue.resolution,
        exchanges=ExchangeProgress(
            used=dialogue.exchanges_used,
            remaining=dialogue.exchanges_remaining,
            cap=dialogue.exchange_cap,
        ),
        exchange_records=[
            DialogueExchangeView(
                exchange_number=exchange.exchange_number,
                guiding_question=exchange.guiding_question,
                probing_focus=exchange.probing_focus,
                asked_at=exchange.asked_at.isoformat(),
                learner_messages=[
                    DialogueMessageView(
                        text=message.text,
                        intent=message.intent.value,
                        received_at=message.received_at.isoformat(),
                    )
                    for message in exchange.learner_messages
                ],
            )
            for exchange in dialogue.exchanges
        ],
        context={
            "naric_level": dialogue.naric_level,
            "naric_level_source": dialogue.naric_level_source,
            "explanation_profile": dialogue.explanation_profile,
            "practice_area": dialogue.practice_area,
            "source_status": dialogue.source_status,
        },
        created_at=dialogue.created_at.isoformat(),
        closed_at=dialogue.closed_at.isoformat() if dialogue.closed_at else None,
    )


# --------------------------------------------------------------------------
# Dev helper -- gated off by default
# --------------------------------------------------------------------------


@router.post("/socratic/dev/sessions", status_code=status.HTTP_201_CREATED)
async def mint_session(container: Container = Depends(container_dep)) -> dict[str, str]:
    """Mint an opaque session id for standalone runs.

    UC-05 receives a ``session_id`` and never creates one on a production path.
    Gated by ``ALLOW_DEV_SESSION_IDS``, which defaults to false; when it is
    false this returns 404, the same as an endpoint that does not exist.
    """
    return {"session_id": mint_dev_session_id(container.settings)}


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@router.get("/healthz", response_model=HealthResponse)
async def healthz(container: Container = Depends(container_dep)) -> HealthResponse:
    """Liveness only.

    Deliberately does not report which providers are bound: provider names must
    not cross the API boundary.
    """
    return HealthResponse(
        status="ok", exchange_cap=container.settings.socratic_exchange_cap
    )
