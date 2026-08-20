"""Request-scoped access to the module's composition root, and the two checks every route performs.

The container is built once at start-up and attached to ``app.state``. Tests build their own
container with fakes and attach it the same way, so the HTTP layer never knows whether it is talking
to the real UC-01…UC-08 modules or to doubles.

**The learner is not in the path.** UC-09 shipped with ``/learners/{id}/…`` and an
``ensure_learner_scope`` helper checking the path against an authenticated header, because it had
no identity layer to consult. Neither survives the merge: the learner comes from the bearer token
through the one authentication seam, so the path segment that could disagree with it is gone.

That removed a duplicate check, not a guarantee — the ownership rule is unchanged and still
enforced in the services, which re-read every formal attempt scoped to the learner they resolved.

``session_token`` is the second check, and it is what makes the device lock reach the HTTP layer.
The token is read from a header rather than from the body so that a GET can carry it too, and
because a credential in a request body tends to end up in a log of request bodies.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request

from app.core.deps import DbSession
from app.modules.formal_assessment.container import Container, FormalAssessmentAppContext
from app.modules.formal_assessment.domain.device import DeviceDescriptor
from app.modules.identity.security import AssessorIdentity, LearnerIdentity, SystemActor

#: The header the authoritative device presents on every operation against a live formal attempt.
SESSION_HEADER = "X-Formal-Session"

#: State key the application factory publishes the container under.
CONTAINER_STATE_KEY = "formal_assessment"


def get_formal_app_context(request: Request) -> FormalAssessmentAppContext:
    context = getattr(request.app.state, CONTAINER_STATE_KEY, None)
    if context is None:  # pragma: no cover - a wiring mistake, not a runtime condition
        raise RuntimeError("The application was created without a FormalAssessmentAppContext.")
    return context


def get_container(request: Request, db: DbSession) -> Iterator[Container]:
    """UC-09's services, bound to this request's session."""
    yield get_formal_app_context(request).build(db)


ContainerDep = Annotated[Container, Depends(get_container)]

#: The learner every formal-assessment operation is scoped to, from the bearer token.
FormalLearner = LearnerIdentity

#: The named human whose decision releases a certificate. Never a body field.
FormalAssessor = AssessorIdentity

#: A platform-internal caller: the session monitor, the certificate service, the recovery sweep.
FormalSystemActor = SystemActor


def get_session_token(
    x_formal_session: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
) -> str | None:
    """The device session token, if the caller presented one.

    Optional at this layer and required at the service layer, deliberately: the refusal for a
    missing token is the same ``DEVICE_SESSION_CONFLICT`` as for a wrong one, decided by the service
    that knows which session is authoritative. Rejecting it here would put half the device rule in
    the HTTP layer.
    """
    if x_formal_session is None:
        return None
    token = x_formal_session.strip()
    return token or None


SessionTokenDep = Annotated[str | None, Depends(get_session_token)]


def describe_device(
    request: Request,
    *,
    fingerprint: str | None = None,
    platform: str | None = None,
) -> DeviceDescriptor:
    """Build the device descriptor from the request and the client's claims.

    The user agent and the IP address are taken from the request rather than from the body: a client
    cannot choose what address it connected from, and a descriptor whose every field was self-
    reported would be worthless as evidence. None of it decides the lock — see ``domain.device``.
    """
    user_agent = request.headers.get("user-agent")
    client_host = request.client.host if request.client else None
    return DeviceDescriptor(
        fingerprint=fingerprint,
        user_agent=user_agent[:512] if user_agent else None,
        ip_address=client_host,
        platform=platform,
    )
