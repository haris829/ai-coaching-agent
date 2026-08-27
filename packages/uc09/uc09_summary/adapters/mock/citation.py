"""Mock CitationProvider. READ ONLY: retrieval method only.

Scenarios: several authorities, none cited, unavailable.

Every resource returned here is a *citation event* from the session. The mock
never returns an authority that is merely relevant to the topic, because that
is precisely the failure the grounding rule exists to prevent, and a mock that
modelled it would be teaching adapters the wrong lesson.
"""

from __future__ import annotations

from uc09_summary.adapters.mock import scenarios
from uc09_summary.domain.errors import ProviderTimeout, ProviderUnavailable
from uc09_summary.domain.models import Resource

PORT = "citation_provider"


class MockCitationProvider:
    """In-process citation source covering the specified scenario matrix."""

    @classmethod
    def from_settings(cls, settings: object) -> MockCitationProvider:
        return cls()

    def for_session(self, session_id: str) -> tuple[Resource, ...]:
        """Return the authorities actually cited during the session."""
        if session_id == scenarios.SESSION_CITATIONS_UNAVAILABLE:
            raise ProviderUnavailable(PORT, "scenario_source_unavailable")
        if session_id == scenarios.SESSION_TIMEOUT:
            raise ProviderTimeout(PORT, "scenario_source_timeout")

        return tuple(scenarios.CITATIONS.get(session_id, ()))

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        return {
            "known_id": scenarios.SESSION_COMPLETE,
            "expected_min_records": 2,
            "empty_id": scenarios.SESSION_NO_CITATIONS,
            "unavailable_id": scenarios.SESSION_CITATIONS_UNAVAILABLE,
            "timeout_id": scenarios.SESSION_TIMEOUT,
            "upstream_tokens": ("MockCitationProvider", "scenarios."),
        }
