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

    # Tokens are credentials for the placeholder directory, so listing them is a decision, not a
    # detail. Two situations want it and they are not the same situation:
    #
    # * local development and the test suite, where there is nothing to protect;
    # * a **review deployment** — public URL, real guards, real database — whose entire purpose is
    #   for someone to try the system out, and who therefore has to be handed a way in.
    #
    # The second is why `DEMO_IDENTITIES` exists rather than this being a check on `ENVIRONMENT`
    # alone. A deployed environment must set `ADMIN_API_TOKEN` to have working guards at all
    # (`Settings._require_credentials_outside_development`), which puts it outside the development
    # safe-list — and a reviewer would then face an API with no way to authenticate to it. Making
    # the switch explicit resolves that without weakening anything: real production leaves
    # `DEMO_IDENTITIES` unset, the list disappears, and the only way in is a credential the
    # operator issued.
    if settings.demo_identities or settings.environment.lower() in {"development", "test"}:
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
