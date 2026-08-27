"""SessionProvider port. READ ONLY."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc09_summary.domain.models import SessionRecord


@runtime_checkable
class SessionProvider(Protocol):
    """Retrieves a coaching session. Declares no mutating method, by design."""

    def get_session(self, session_id: str) -> SessionRecord:
        """Return the session for ``session_id``.

        Args:
            session_id: opaque platform session identifier. This component
                receives it and never mints one on a production path.

        Returns:
            A fully normalised :class:`SessionRecord`. The NARIC level is
            already a platform enum whatever the upstream sent, and the course
            completion percentage is already an integer 0-100.

        Raises:
            SessionNotFound: upstream answered, and there is no such session.
                Distinct from unavailable: the source was reachable.
            ProviderUnavailable: upstream unreachable, refused, or errored.
            ProviderTimeout: upstream exceeded the configured deadline.
            ProviderInvalidResponse: upstream answered with a payload that
                cannot be mapped onto the platform contract.
        """
        ...
