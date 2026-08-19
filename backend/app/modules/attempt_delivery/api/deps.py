"""FastAPI dependencies: learner identity, request id and the per-request context."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from app.modules.attempt_delivery.container import AppContext, RequestContext
from app.modules.attempt_delivery.domain import errors
from app.modules.identity.principal import Role
from app.modules.identity.security import OptionalPrincipal


def get_app_context(request: Request) -> AppContext:
    context = getattr(request.app.state, "context", None)
    if context is None:  # pragma: no cover - application wiring error
        raise RuntimeError("The application was created without an AppContext.")
    return context


def get_request_context(request: Request) -> Iterator[RequestContext]:
    """Provide a session-scoped :class:`RequestContext` for one request.

    Services commit their own units of work, so this dependency only guarantees the
    session is rolled back on an unhandled error and always closed.
    """
    app_context = get_app_context(request)
    session = app_context.session_factory()
    try:
        yield app_context.build(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_learner_id(principal: OptionalPrincipal) -> str:
    """Resolve the learner identity through the application's one authentication seam.

    UC-03 does not own authentication. It previously read an ``X-Learner-Id`` header on the
    assumption that a gateway had already authenticated the learner; now that UC-01 and UC-02 are
    merged in, there is a real identity seam (``app.modules.identity``) and using anything else
    would be a second, weaker way in.

    Only a **learner** may drive an attempt: an administrator token is rejected rather than
    silently treated as a learner, which would let an admin consume someone's attempt allowance.

    Every attempt lookup is then scoped to the returned id in the repository layer, so
    authorisation does not depend on each handler remembering to check.
    """
    if principal is None:
        raise errors.unauthenticated(
            "A learner identity is required. Supply a bearer token for a learner."
        )
    if principal.role is not Role.LEARNER:
        raise errors.unauthenticated(
            "Quiz attempts are learner-scoped; this credential is not a learner's."
        )
    return str(principal.id)


async def attach_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Correlation id middleware.

    Returned as ``X-Request-Id`` and echoed in every error body, so an operator can tie
    a learner's report to the exact server-side log entry without the response ever
    carrying a traceback.
    """
    incoming = request.headers.get("x-request-id", "").strip()
    request_id = incoming or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


LearnerId = Annotated[str, Depends(get_learner_id)]
Context = Annotated[RequestContext, Depends(get_request_context)]
