"""CurrentUserProvider - a minimal, replaceable identity abstraction.

Deliberately not an authentication system. UC-06 resolves user_id server-side and
never reads it from a request body; the shipped adapter reads a trusted header
placed by an upstream gateway. Replacing it with real authentication is one
adapter file and one registry line.
"""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable


@runtime_checkable
class CurrentUserProvider(Protocol):
    """Resolve the acting user from request metadata.

    `headers` is a read-only mapping of request headers. The request BODY is not
    passed and must never be consulted: user_id is never client-asserted content.

    Raises ProviderUnavailable when identity cannot be established.
    """

    def resolve(self, headers: Mapping[str, str]) -> str:
        ...
