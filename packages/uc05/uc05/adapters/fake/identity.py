"""Minimal, replaceable identity.

UC-05 does not implement production authentication.  What matters here is the
single invariant the brief fixes: ``user_id`` is resolved **server-side** and
is never read from a request body.  This adapter reads a header; a company
adapter validates a bearer token and returns the subject.  Nothing else in
UC-05 changes.
"""

from __future__ import annotations

from typing import Any

from ...domain.errors import ProviderUnavailable
from ...registry import CURRENT_USER_REGISTRY

PORT = "current_user_provider"
USER_HEADER = "X-User-Id"


@CURRENT_USER_REGISTRY.register("header")
class HeaderCurrentUserProvider:
    """Development identity: trusts a header.  Never for production use."""

    def __init__(self, header_name: str = USER_HEADER, **_: object) -> None:
        self.header_name = header_name

    async def resolve(self, request: Any) -> str:
        headers = getattr(request, "headers", None)
        value = headers.get(self.header_name) if headers is not None else None
        if not value or not str(value).strip():
            raise ProviderUnavailable(PORT, "no identity on request")
        return str(value).strip()


@CURRENT_USER_REGISTRY.register("static")
class StaticCurrentUserProvider:
    """Fixed identity, for service-level tests that bypass the transport."""

    def __init__(self, user_id: str = "learner-1", **_: object) -> None:
        self.user_id = user_id

    async def resolve(self, request: Any) -> str:
        return self.user_id
