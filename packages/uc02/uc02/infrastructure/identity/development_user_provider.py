"""Replaceable identity shim.

UC-02 ships no production auth. It needs exactly one fact per request: the
authenticated user id. This implementation reads it from a configurable header
so the service is runnable and testable standalone.

The rule that matters and does not change when this class is replaced: the user
id is resolved server-side. Nothing in the request body can influence it.

Replacement: implement ``CurrentUserProvider`` against the platform's real auth
(validate the bearer token / session cookie, return the subject claim) and swap
it in ``uc02/composition.py``. Nothing else in the codebase reads identity.
"""

from __future__ import annotations

from typing import Any

from uc02.domain.errors import IdentityResolutionFailed
from uc02.domain.ports.identity import CurrentUserProvider


class DevelopmentUserProvider(CurrentUserProvider):
    """Trusts a header. Acceptable for local development and tests only."""

    def __init__(self, header_name: str = "X-User-Id") -> None:
        self._header_name = header_name

    @property
    def header_name(self) -> str:
        return self._header_name

    async def resolve(self, request: Any) -> str:
        headers = getattr(request, "headers", None)
        user_id = headers.get(self._header_name) if headers is not None else None
        if not user_id or not user_id.strip():
            raise IdentityResolutionFailed(
                f"{self._header_name} header missing; caller is unauthenticated"
            )
        return user_id.strip()


class StaticUserProvider(CurrentUserProvider):
    """Always resolves to one user id. Used by tests that do not exercise auth."""

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    async def resolve(self, request: Any) -> str:
        return self._user_id
