"""Identity abstraction.

A placeholder for whatever the platform gateway injects (JWT subject, session cookie, mTLS
identity). It reads a header, never the request body, and it is replaced by a real adapter
through the same registry as every other port.
"""

from __future__ import annotations

from ...domain.errors import AccessDenied

HEADER = "x-user-id"


class HeaderCurrentUserProvider:
    name = "header"

    def resolve(self, headers: dict[str, str]) -> str:
        value = headers.get(HEADER) or headers.get(HEADER.title()) or headers.get(HEADER.upper())
        if not value or not value.strip():
            raise AccessDenied("no authenticated principal")
        return value.strip()
