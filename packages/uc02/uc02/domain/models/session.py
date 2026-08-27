"""Session identity — the input contract for context initialisation.

UC-02 does not own the session lifecycle. UC-01 creates sessions; UC-02 owns the
*context* keyed by a session id it is handed. In production UC-02 never invents a
session id. See docs/integration.md.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionIdentity(BaseModel):
    """Who is initialising context, for which session, and when.

    ``session_id`` is an opaque string. UC-02 must not parse it, validate its
    format beyond non-emptiness, or derive meaning from it.

    ``user_id`` is always the value resolved by ``CurrentUserProvider``. It is
    never read from a request body.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    requested_at: datetime
    session_id_origin: str = Field(
        default="caller",
        description="'caller' in production; 'dev-minted' only when ALLOW_DEV_SESSION_IDS=true.",
    )
