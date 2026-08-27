"""Session identity.

UC-08 **receives** an opaque ``session_id``. It never creates one on a
production path. A dev-mode mint exists for local work, is gated by
``ALLOW_DEV_SESSION_MINTING``, and that flag defaults to off -- so the
production behaviour of a missing session id is a visible error, not a quietly
invented identifier.

The minted value is derived from the account and the clock, never from a random
source, so a dev run is reproducible and a minted id is recognisable on sight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from uc08.domain.enums import SessionIdSource
from uc08.domain.errors import SessionIdRequired
from uc08.logging_setup import get_logger

_log = get_logger(__name__)

DEV_SESSION_PREFIX = "dev-minted-session"


@dataclass(frozen=True)
class ResolvedSession:
    session_id: str
    source: SessionIdSource


def resolve_session_id(
    provided: str | None,
    *,
    user_id: str,
    now: datetime,
    allow_dev_minting: bool,
) -> ResolvedSession:
    if provided is not None and provided.strip():
        return ResolvedSession(provided.strip(), SessionIdSource.RECEIVED)

    if not allow_dev_minting:
        raise SessionIdRequired(
            "session_id is required: UC-08 receives an opaque session id and does not create one. "
            "Set ALLOW_DEV_SESSION_MINTING=true for local development only."
        )

    minted = f"{DEV_SESSION_PREFIX}-{user_id}-{now.strftime('%Y%m%dT%H%M%S%f')}Z"
    _log.warning(
        "dev_session_id_minted",
        extra={"user_id": user_id, "session_id_source": SessionIdSource.DEV_MINTED.value},
    )
    return ResolvedSession(minted, SessionIdSource.DEV_MINTED)
