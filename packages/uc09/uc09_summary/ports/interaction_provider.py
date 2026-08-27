"""InteractionProvider port. READ ONLY."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc09_summary.domain.models import InteractionRecord


@runtime_checkable
class InteractionProvider(Protocol):
    """Retrieves the logged interactions of a session. No mutating method."""

    def for_session(self, session_id: str) -> tuple[InteractionRecord, ...]:
        """Return every logged interaction for the session, oldest first.

        The topic and concept tags on these records are the **only** admissible
        source for the Topics Covered and Key Concepts sections.

        Returns:
            A possibly empty tuple. Empty means the session legitimately has no
            logged interaction; it is never used to signal a failure.

        Raises:
            ProviderUnavailable: upstream unreachable, refused, or errored.
            ProviderTimeout: upstream exceeded the configured deadline.
            ProviderInvalidResponse: payload cannot be mapped onto the contract.
        """
        ...
