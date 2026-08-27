"""Identity and time ports.

``CurrentUserProvider`` is deliberately the *only* way UC-07 learns who the
caller is. The API never accepts a user id in a path, query string or body.

``Clock`` exists so report timestamps are injectable and therefore testable /
deterministic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class CurrentUserProvider(ABC):
    """Resolves the server-side learner identity for a request.

    This is a replaceable seam, not production authentication.
    """

    @abstractmethod
    def resolve(self, request: Any) -> str:
        """Return the resolved user id, or raise :class:`IdentityUnresolved`."""


class IdentityUnresolved(Exception):
    """No server-side identity could be resolved for the request."""

    code = "identity_unresolved"


class Clock(ABC):
    """Time as a port, so report generation is deterministic under test."""

    @abstractmethod
    def now(self) -> datetime:
        """Current timezone-aware UTC time."""
