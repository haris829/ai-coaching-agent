from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CurrentUserProvider(Protocol):
    """Resolves the acting user from the transport.

    Deliberately minimal and replaceable: UC-05 does not implement production
    authentication.  ``user_id`` is resolved here, server-side, and is never
    read from a request body.

    Raises:
        ProviderUnavailable: identity could not be established.
    """

    async def resolve(self, request: Any) -> str:
        ...
