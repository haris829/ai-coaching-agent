"""Identity resolution and route guards — the single authentication seam.

One credential works across the whole API. A bearer token is resolved in this order:

1. a row in ``qa_users`` (the local placeholder directory) — yields that user's role;
2. ``ADMIN_API_TOKEN``, when configured — yields a service administrator;
3. otherwise the credential is unknown.

Guards
------
``require_principal``       any authenticated caller            401 when unresolved
``require_admin_principal`` an administrator, as a principal    401 unresolved / 403 wrong role
``require_admin``           an administrator, as an actor label 401 / 403, plus the
                            "no token configured" local-development pass-through

The last one exists because the Question Bank endpoints only ever need the audit label, not the
whole principal, and because they must keep working with the guard switched off in local
development. When ``ADMIN_API_TOKEN`` is unset **and** no credential is supplied, writes are
allowed and attributed to the ``X-Admin-User`` header — the documented local-development mode.
Setting ``ADMIN_API_TOKEN`` closes that door.

Replacing this file's :func:`resolve_principal` with the company's real dependency is the whole
of the identity integration; no business rule reads a user row directly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import DbSession
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import bearer_token, normalise_actor
from app.modules.identity.models import User
from app.modules.identity.principal import Principal, Role
from app.modules.identity.repository import SqlAlchemyUserRepository

#: Identifier used for a caller authenticated by ``ADMIN_API_TOKEN`` rather than a directory row.
SERVICE_ADMIN_ID = 0


# No `alias=` here: pydantic ignores field metadata attached to a reusable ``Annotated`` alias, so
# an alias would silently do nothing. FastAPI derives the header name from the parameter name —
# `authorization` → `Authorization`, `x_admin_user` → `X-Admin-User` — which is what we want anyway,
# and header matching is case-insensitive.
AuthorizationHeader = Annotated[str | None, Header()]
AdminUserHeader = Annotated[str | None, Header()]


def principal_from_user(user: User) -> Principal:
    return Principal(
        id=user.id,
        display_name=user.display_name,
        role=Role(user.role),
        actor=normalise_actor(user.email, default=f"user:{user.id}"),
    )


def resolve_principal(
    db: Session, authorization: str | None, admin_user_header: str | None
) -> Principal | None:
    """Turn a credential into a principal, or ``None`` when it cannot be resolved."""
    token = bearer_token(authorization)
    if token is None:
        return None

    user = SqlAlchemyUserRepository(db).get_by_token(token)
    if user is not None:
        return principal_from_user(user)

    if settings.admin_api_token and token == settings.admin_api_token:
        return Principal(
            id=SERVICE_ADMIN_ID,
            display_name="Service Administrator",
            role=Role.ADMIN,
            actor=normalise_actor(admin_user_header),
        )

    return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def optional_principal(
    db: DbSession,
    authorization: AuthorizationHeader = None,
    x_admin_user: AdminUserHeader = None,
) -> Principal | None:
    return resolve_principal(db, authorization, x_admin_user)


def require_principal(
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> Principal:
    if principal is None:
        raise UnauthorizedError("Provide a bearer token to identify yourself.")
    return principal


def require_learner_principal(
    principal: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    """A learner, as a principal.

    Results, pass/fail outcomes and feedback are all learner-scoped: they belong to whoever sat the
    attempt. An administrator credential is rejected rather than silently treated as a learner,
    which is the same rule UC-03 applies to driving an attempt -- and it lives here, in the one
    authentication seam, rather than being re-derived by each capability that needs it.
    """
    if principal.role is not Role.LEARNER:
        raise ForbiddenError("This resource is learner-scoped; this credential is not a learner's.")
    return principal


def require_learner_id(
    principal: Annotated[Principal, Depends(require_learner_principal)],
) -> str:
    """The learner's id as the opaque string every capability stores it as."""
    return str(principal.id)


def require_admin_principal(
    principal: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    if not principal.is_admin:
        raise ForbiddenError("This action requires the admin role.")
    return principal


def require_admin(
    principal: Annotated[Principal | None, Depends(optional_principal)],
    x_admin_user: AdminUserHeader = None,
) -> str:
    """Authorise a mutating administrative call and return the actor to attribute it to."""
    if principal is not None:
        if not principal.is_admin:
            raise ForbiddenError("This action requires the admin role.")
        return principal.actor

    if settings.admin_api_token:
        raise UnauthorizedError("An administrator bearer token is required.")

    # Local development: no credential configured, so writes are open and attributed to the
    # X-Admin-User header. Setting ADMIN_API_TOKEN closes this path.
    return normalise_actor(x_admin_user)


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
AdminPrincipal = Annotated[Principal, Depends(require_admin_principal)]
LearnerPrincipal = Annotated[Principal, Depends(require_learner_principal)]
LearnerIdentity = Annotated[str, Depends(require_learner_id)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]
Actor = Annotated[str, Depends(require_admin)]
