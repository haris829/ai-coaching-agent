"""Identity port.

Deliberately minimal and deliberately replaceable. This component performs no
production authentication: it resolves a caller to a ``user_id`` and enforces
ownership against it. Whatever the platform eventually uses - a session cookie,
a bearer token, a gateway-injected header - becomes one adapter behind this
interface, and nothing else in the component changes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CurrentUserProvider(Protocol):
    """Resolves the caller of a request to a platform user id."""

    def resolve(self, request: Any) -> str:
        """Return the caller user id.

        Args:
            request: the inbound request object.

        Returns:
            An opaque platform user id.

        Raises:
            IdentityUnresolved: no identity could be established. The caller
                gets an authentication failure and no record is disclosed.
        """
        ...
