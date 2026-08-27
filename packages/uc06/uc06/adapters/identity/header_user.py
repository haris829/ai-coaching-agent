"""HeaderCurrentUserProvider - minimal, replaceable identity.

Not authentication. It reads a user identifier from a header that a trusted
upstream gateway is expected to set, which is the smallest abstraction that lets
UC-06 resolve user_id server-side and never from a request body.

Replacing this with real authentication (session cookie, OIDC token, mTLS
identity) is one adapter file and one registry line. No caller changes: everything
downstream sees a `user_id` string.
"""

from __future__ import annotations

from typing import Final, Mapping

from ...config import Settings
from ...domain.errors import ProviderUnavailable

USER_HEADER: Final = "x-uc06-user-id"


class HeaderCurrentUserProvider:
    """Implements CurrentUserProvider."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def resolve(self, headers: Mapping[str, str]) -> str:
        # Header lookup is case-insensitive; Starlette already normalises, but an
        # adapter must not depend on its caller doing so.
        for key, value in headers.items():
            if key.lower() == USER_HEADER and value.strip():
                return value.strip()
        raise ProviderUnavailable("current_user_provider", "no_identity_on_request")
