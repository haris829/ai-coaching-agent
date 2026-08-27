"""GapReportProvider port. READ ONLY."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc09_summary.domain.models import Suggestion


@runtime_checkable
class GapReportProvider(Protocol):
    """Retrieves gap-analysis suggestions for a learner. No mutating method.

    This component does not perform gap analysis and knows nothing about how a
    gap is computed. It consumes suggestions and nothing more.
    """

    def suggestions(self, user_id: str) -> tuple[Suggestion, ...] | None:
        """Return gap-report suggestions for the learner.

        Returns:
            A tuple of suggestions, possibly empty when the gap report ran and
            found nothing to suggest; or ``None`` when no gap report is
            available for this learner. The two are different states and must
            not be conflated: empty degrades Next Steps to session-derived
            suggestions, ``None`` does the same but is reported as
            ``unavailable`` rather than ``empty``.

        Raises:
            ProviderUnavailable: upstream unreachable, refused, or errored.
            ProviderTimeout: upstream exceeded the configured deadline.
            ProviderInvalidResponse: payload cannot be mapped onto the contract.
        """
        ...
