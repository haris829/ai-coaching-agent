"""UC-02 HTTP surface.

One primary endpoint, internal service-to-service. Two secondary endpoints, both
config-gated and both off by default.

Deliberately absent: any endpoint that returns a full ``SessionContext`` to a
client on the public path. The assembled context holds a learner's qualification
level, practice speciality and question history; it is private server-side data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from uc02.api.v1.schemas import (
    ContextStatusResponse,
    InitializeContextRequest,
    InitializeContextResponse,
)
from uc02.application.context_assembly_service import ContextAssemblyService, utc_now
from uc02.composition import (
    get_assembly_service,
    get_current_user_provider,
    resolve_session_id,
)
from uc02.domain.errors import ForceRefreshNotPermitted
from uc02.domain.models.context import SessionContext
from uc02.domain.models.enums import ContextStatus
from uc02.domain.models.session import SessionIdentity
from uc02.domain.ports.identity import CurrentUserProvider
from uc02.infrastructure.config.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["context"])


async def _identity(
    request: Request,
    session_id: str | None,
    user_provider: CurrentUserProvider,
    settings: Settings,
) -> SessionIdentity:
    """Build the input contract: caller-supplied session, server-resolved user."""
    user_id = await user_provider.resolve(request)
    resolved_session_id, origin = resolve_session_id(session_id, settings)
    return SessionIdentity(
        session_id=resolved_session_id,
        user_id=user_id,
        requested_at=utc_now(),
        session_id_origin=origin,
    )


@router.post(
    "/context/initialize",
    response_model=InitializeContextResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assemble (or return) the learning context for a session",
)
async def initialize_context(
    request: Request,
    response: Response,
    body: InitializeContextRequest,
    service: ContextAssemblyService = Depends(get_assembly_service),
    user_provider: CurrentUserProvider = Depends(get_current_user_provider),
    settings: Settings = Depends(get_settings),
) -> InitializeContextResponse:
    """Build context once at session start; return the stored context thereafter.

    201 when this call built the context, 200 when it returned a stored one.
    ``force_refresh`` is always rejected here regardless of configuration -- the
    only path that honours it is the internal admin endpoint below.
    """
    if body.force_refresh:
        raise ForceRefreshNotPermitted(
            "force_refresh is not available on the public initialize path"
        )

    identity = await _identity(request, body.session_id, user_provider, settings)
    outcome = await service.initialize(identity)
    response.status_code = (
        status.HTTP_201_CREATED
        if outcome.status is ContextStatus.CREATED
        else status.HTTP_200_OK
    )
    return InitializeContextResponse.from_context(outcome.context, outcome.status)


@router.get(
    "/context/{session_id}/status",
    response_model=ContextStatusResponse,
    summary="Status flags for a session's context (never its content)",
)
async def context_status(
    request: Request,
    session_id: str,
    service: ContextAssemblyService = Depends(get_assembly_service),
    user_provider: CurrentUserProvider = Depends(get_current_user_provider),
) -> ContextStatusResponse:
    """Ownership is enforced: a session id alone is not sufficient."""
    user_id = await user_provider.resolve(request)
    context = await service.get_for_user(session_id, user_id)
    return ContextStatusResponse.from_context(context)


@router.post(
    "/internal/context/{session_id}/refresh",
    response_model=InitializeContextResponse,
    summary="Admin-only rebuild. Disabled unless ALLOW_FORCE_REFRESH=true",
)
async def force_refresh_context(
    request: Request,
    session_id: str,
    service: ContextAssemblyService = Depends(get_assembly_service),
    user_provider: CurrentUserProvider = Depends(get_current_user_provider),
    settings: Settings = Depends(get_settings),
) -> InitializeContextResponse:
    """Rebuild a session's context, re-querying every provider.

    Gated twice: the config flag must be on, and the caller must present the
    internal admin header. Off by default, and never reachable from the public
    initialize path.
    """
    if not settings.allow_force_refresh:
        _not_found()
    if not request.headers.get(settings.internal_admin_header):
        _not_found()

    identity = await _identity(request, session_id, user_provider, settings)
    outcome = await service.initialize(identity, force_refresh=True)
    return InitializeContextResponse.from_context(outcome.context, outcome.status)


@router.get(
    "/internal/context/{session_id}/debug",
    response_model=SessionContext,
    summary="Full context dump. Disabled unless DEBUG_CONTEXT_ENDPOINT=true",
)
async def debug_context(
    request: Request,
    session_id: str,
    service: ContextAssemblyService = Depends(get_assembly_service),
    user_provider: CurrentUserProvider = Depends(get_current_user_provider),
    settings: Settings = Depends(get_settings),
) -> SessionContext:
    """The only endpoint that returns a full context, and it is off by default.

    ``Settings.production_guard_violations`` refuses this flag in production and
    a test asserts the endpoint answers 404 under production configuration.
    """
    if not settings.debug_context_endpoint:
        _not_found()
    user_id = await user_provider.resolve(request)
    return await service.get_for_user(session_id, user_id)


def _not_found() -> None:
    """Disabled endpoints answer 404, not 403: they should look absent."""
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
