"""Transport-level credential primitives.

Deliberately free of database access so ``app.core`` carries no dependency on any module.
Identity resolution — turning a credential into a principal with a role — lives in
``app/modules/identity``, which is the seam the company's real identity provider replaces.
"""

from __future__ import annotations

ACTOR_HEADER = "X-Admin-User"
DEFAULT_ACTOR = "admin"
MAX_ACTOR_LENGTH = 128


def bearer_token(authorization: str | None) -> str | None:
    """Extract a bearer token from an ``Authorization`` header. ``None`` when absent/malformed."""
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = value.strip()
    return token or None


def normalise_actor(raw: str | None, *, default: str = DEFAULT_ACTOR) -> str:
    """Clamp a caller-supplied actor label to something safe to store in an audit column."""
    actor = (raw or "").strip()
    return actor[:MAX_ACTOR_LENGTH] if actor else default
