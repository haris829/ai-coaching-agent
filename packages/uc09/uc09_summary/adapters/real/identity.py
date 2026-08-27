"""Minimal identity adapter.

This is **not** production authentication and does not pretend to be. It reads
a user id from a request header, which is what a gateway that has already
authenticated the caller would supply.

The replacement point is this file and the ``current_user_provider`` registry
entry. Whatever the platform settles on - a signed session cookie, a bearer
token validated against a JWKS, a gateway assertion - becomes one adapter here,
and no service, route or test changes. Ownership enforcement lives in the
application layer against whatever ``user_id`` this returns, so the
authorisation rule does not move when the authentication mechanism does.
"""

from __future__ import annotations

from typing import Any

from uc09_summary.domain.errors import IdentityUnresolved

#: Header carrying the already-authenticated caller.
USER_HEADER = "X-User-Id"


class HeaderIdentityProvider:
    """Resolves the caller from a gateway-supplied header."""

    @classmethod
    def from_settings(cls, settings: object) -> HeaderIdentityProvider:
        return cls()

    def resolve(self, request: Any) -> str:
        """Return the caller user id from the request header.

        Raises:
            IdentityUnresolved: the header is absent or blank.
        """
        headers = getattr(request, "headers", None)
        value = headers.get(USER_HEADER) if headers is not None else None
        if not value or not value.strip():
            raise IdentityUnresolved(
                f"No caller identity: {USER_HEADER} header absent or blank."
            )
        return value.strip()
