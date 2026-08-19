"""Identity endpoint.

``GET /session`` answers "who am I". It also lists the local development identities and their
tokens so the test UI can switch between an administrator and a learner without a login screen —
a **development convenience** that exists only because this module is a placeholder. When the
company's identity provider is wired in, the ``users`` list goes away and only ``user`` remains.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.config import settings
from app.core.deps import DbSession
from app.modules.identity.repository import SqlAlchemyUserRepository
from app.modules.identity.security import OptionalPrincipal

router = APIRouter(tags=["Identity"])



@router.get("/session", summary="The current caller, and the local development identities")
def session_info(db: DbSession, principal: OptionalPrincipal) -> dict[str, Any]:
    body: dict[str, Any] = {
        "user": (
            None
            if principal is None
            else {
                "id": principal.id,
                "displayName": principal.display_name,
                "role": principal.role.value,
            }
        )
    }

    # Tokens are development credentials for the placeholder directory. Never expose them once a
    # real identity provider is in place, and never outside a development environment.
    if settings.environment.lower() in {"development", "test"}:
        body["users"] = [
            {
                "id": user.id,
                "displayName": user.display_name,
                "role": user.role,
                "email": user.email,
                "token": user.api_token,
            }
            for user in SqlAlchemyUserRepository(db).list_all()
        ]

    return body
