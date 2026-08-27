"""CitationProvider port. READ ONLY."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc09_summary.domain.models import Resource


@runtime_checkable
class CitationProvider(Protocol):
    """Retrieves the authorities actually cited during a session. No mutating method."""

    def for_session(self, session_id: str) -> tuple[Resource, ...]:
        """Return the authorities cited **during this session**.

        This is a record of what was cited, not a reading list. An adapter that
        returns authorities merely relevant to the session topic breaks the
        grounding guarantee at its source, and no downstream check can repair
        that - the check can only confirm the summary matches what this port
        said. Implementations must return citation events, nothing else.

        Returns:
            A possibly empty tuple. Empty means nothing was cited, which is a
            legitimate and reportable outcome.

        Raises:
            ProviderUnavailable: upstream unreachable, refused, or errored.
            ProviderTimeout: upstream exceeded the configured deadline.
            ProviderInvalidResponse: payload cannot be mapped onto the contract.
        """
        ...
