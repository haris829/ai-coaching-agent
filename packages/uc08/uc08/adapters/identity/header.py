"""Minimal replaceable identity.

The account id arrives in a request header set by the edge, and this adapter is
the only thing that knows that. Replacing it with the platform authentication
(a bearer token, a session cookie, a service mesh identity) means writing one
class and one registry line -- see ``docs/INTEGRATION.md``.

This is explicitly **not** production authentication. It does not verify a
signature, it does not check an audience, and it must not be deployed as-is.
What it does guarantee is the invariant that matters here: the identity comes
from the request context, never from a path segment, query parameter or body
field, so no learner can read or write another learner state by editing a URL.
"""

from __future__ import annotations

from typing import Any

from uc08.logging_setup import get_logger
from uc08.ports.identity import CurrentUserProvider, IdentityNotResolved

_log = get_logger(__name__)


class HeaderCurrentUserProvider(CurrentUserProvider):
    """Reads the account from a request header set by the edge (A-22)."""

    def __init__(self, header_name: str = "X-UC08-Subject") -> None:
        self._header_name = header_name

    @property
    def header_name(self) -> str:
        return self._header_name

    def resolve(self, request: Any) -> str:
        headers = getattr(request, "headers", None)
        raw = headers.get(self._header_name) if headers is not None else None
        if raw is None or not str(raw).strip():
            _log.info("identity_not_resolved", extra={"header": self._header_name})
            raise IdentityNotResolved("no authenticated subject on the request")
        subject = str(raw).strip()
        if len(subject) > 200:
            raise IdentityNotResolved("subject identifier is implausibly long")
        return subject
