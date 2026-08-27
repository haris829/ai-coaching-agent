"""DEVELOPMENT-ONLY user context provider.

Implements :class:`uc01.contracts.services.UserContextProvider`.

This is **not** a security control. It exists so that authorization logic (session
ownership, course access, case access) can be written and tested against a stable,
server-resolved identity while the company authentication system is unavailable.

Replacement: implement ``resolve()`` against the real token verifier (JWT signature +
claims, session cookie, mTLS subject, ...) and register it in
``uc01/api/container.py``. Nothing else in UC-01 changes.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from ..domain.errors import AuthenticationRequiredError
from ..domain.models import UserContext
from .mock.fixtures import DEV_TOKEN_TO_USER, DEV_USERS

logger = logging.getLogger(__name__)


class DevHeaderUserContextProvider:
    """Resolves a caller from a development token.

    Accepted credential formats (both server-side lookups, never trusted as-is):

    * ``Authorization: Bearer dev-alice``
    * ``X-Dev-User: dev-alice``

    An unknown or missing token is an authentication failure. A user id supplied in a
    request body is ignored everywhere in this project.
    """

    def __init__(self, token_directory: Mapping[str, str] | None = None) -> None:
        self._directory = dict(token_directory or DEV_TOKEN_TO_USER)

    def resolve(self, credential: str | None) -> UserContext:
        token = (credential or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            raise AuthenticationRequiredError()
        user_id = self._directory.get(token)
        if user_id is None:
            logger.info(
                "auth.unknown_token",
                extra={"uc01": {"token_prefix": token[:4], "token_length": len(token)}},
            )
            raise AuthenticationRequiredError()
        return UserContext(user_id=user_id)

    @staticmethod
    def dev_directory() -> Mapping[str, Mapping[str, str]]:
        """Development helper used by the reference UI's user switcher."""
        return DEV_USERS


__all__ = ["DevHeaderUserContextProvider"]
