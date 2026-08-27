"""Minimal, replaceable identity abstraction. Not production authentication."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CurrentUserProvider(Protocol):
    def resolve(self, request: Any) -> str | None:
        """Server-side learner identity, or None when the caller is anonymous.

        ``user_id`` is NEVER taken from a request body."""
        ...


@runtime_checkable
class AdminIdentityProvider(Protocol):
    """ASSUMED BY US (A-15): a structurally separate admin identity.

    Admin authority is a different port, not a role flag on the learner principal, so no
    learner request path can produce an admin principal.
    """

    def resolve_admin(self, request: Any) -> str | None:
        ...
