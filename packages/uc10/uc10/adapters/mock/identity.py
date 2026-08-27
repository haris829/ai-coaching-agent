"""Minimal, replaceable identity. NOT production authentication.

Learner identity and admin authority are two separate ports with two separate
credentials.  A learner credential can never be turned into an admin principal, because
nothing in the learner path issues the admin credential -- and when no admin credential
is configured server-side, the admin provider denies every request rather than falling
back to trusting a header.

Replacing this with real authentication is one new adapter and one registry line; no
domain, application or API code changes.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from uc10.config import Settings, get_settings
from uc10.logging_setup import get_logger

log = get_logger("uc10.identity")

LEARNER_HEADER = "X-User-Id"
ADMIN_HEADER = "X-Admin-Token"
ADMIN_ID_HEADER = "X-Admin-Id"


def _header(request: Any, name: str) -> str | None:
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    value = headers.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


class HeaderCurrentUserProvider:
    """Dev CurrentUserProvider. ``user_id`` is resolved server-side from the request
    context and is never read from a request body."""

    def resolve(self, request: Any) -> str | None:
        return _header(request, LEARNER_HEADER)


class ConfiguredAdminIdentityProvider:
    """Dev AdminIdentityProvider gated on an out-of-band shared token.

    With no token configured this denies everything: a component that silently accepted
    self-asserted admin authority would be worse than one that refuses.
    """

    def __init__(self, settings_factory: Callable[[], Settings] = get_settings) -> None:
        self._settings_factory = settings_factory

    def resolve_admin(self, request: Any) -> str | None:
        expected = self._settings_factory().dev_admin_token
        if not expected:
            return None
        presented = _header(request, ADMIN_HEADER)
        if presented is None or not hmac.compare_digest(presented, expected):
            return None
        return _header(request, ADMIN_ID_HEADER) or "admin_dev"
