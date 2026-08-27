"""Identity port.

UC-02 ships no production auth. It needs exactly one thing from the platform's
auth layer: the authenticated user id for the current request. Anything the
request *body* claims about identity is ignored.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CurrentUserProvider(ABC):
    @abstractmethod
    async def resolve(self, request: Any) -> str:
        """Return the authenticated user id for this request.

        ``request`` is intentionally loosely typed so the domain layer does not
        depend on FastAPI. Implementations raise
        ``IdentityResolutionFailed`` when the caller is not authenticated.
        """
