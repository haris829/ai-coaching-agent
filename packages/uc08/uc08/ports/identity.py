"""Minimal, replaceable identity.

This is not production authentication and does not pretend to be. It is the one
seam through which a ``user_id`` enters the component, so that replacing it with
the platform's real authentication touches one adapter and nothing else.

The rule it exists to enforce: no endpoint accepts a user identifier. The
``user_id`` is resolved server-side, from the request, by this port.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from uc08.domain.errors import Uc08Error


class IdentityNotResolved(Uc08Error):
    """The request carried no usable identity. Surfaces as HTTP 401."""


class CurrentUserProvider(ABC):
    @abstractmethod
    def resolve(self, request: Any) -> str:
        """Return the authenticated account id, or raise
        :class:`IdentityNotResolved`.

        Implementations must ignore any user identifier present in the request
        body, query string or path. Only server-side credentials count.
        """
