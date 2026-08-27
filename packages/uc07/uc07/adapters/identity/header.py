"""Replaceable CurrentUserProvider implementations.

This is a development seam, NOT production authentication. In a real deployment
the header provider is replaced by whatever the platform uses (session, JWT,
gateway assertion). What must not change is the rule it enforces: the learner
identity is resolved server-side and never taken from the request path, query
string or body.
"""

from __future__ import annotations

from typing import Any

from uc07.ports.identity import CurrentUserProvider, IdentityUnresolved


class HeaderCurrentUserProvider(CurrentUserProvider):
    """Resolves identity from a trusted header injected by the edge."""

    def __init__(self, header_name: str = "X-User-Id") -> None:
        self._header_name = header_name

    @property
    def header_name(self) -> str:
        return self._header_name

    def resolve(self, request: Any) -> str:
        headers = getattr(request, "headers", None)
        raw = headers.get(self._header_name) if headers is not None else None
        if raw is None or not str(raw).strip():
            raise IdentityUnresolved("no server-side identity available")
        return str(raw).strip()


class StaticCurrentUserProvider(CurrentUserProvider):
    """Fixed identity, useful for local runs and tests."""

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    def resolve(self, request: Any) -> str:  # noqa: ARG002 - identity is fixed
        return self._user_id
