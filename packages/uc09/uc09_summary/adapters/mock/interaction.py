"""Mock InteractionProvider. READ ONLY: retrieval method only.

Scenarios: multi-topic, single-topic, one interaction only, none, unavailable.
"""

from __future__ import annotations

from uc09_summary.adapters.mock import scenarios
from uc09_summary.domain.errors import ProviderTimeout, ProviderUnavailable
from uc09_summary.domain.models import InteractionRecord

PORT = "interaction_provider"


class MockInteractionProvider:
    """In-process interaction source covering the specified scenario matrix."""

    @classmethod
    def from_settings(cls, settings: object) -> MockInteractionProvider:
        return cls()

    def for_session(self, session_id: str) -> tuple[InteractionRecord, ...]:
        """Return the logged interactions for the session, oldest first."""
        if session_id == scenarios.SESSION_INTERACTIONS_UNAVAILABLE:
            raise ProviderUnavailable(PORT, "scenario_source_unavailable")
        if session_id == scenarios.SESSION_TIMEOUT:
            raise ProviderTimeout(PORT, "scenario_source_timeout")

        records = scenarios.INTERACTIONS.get(session_id, ())
        return tuple(sorted(records, key=lambda r: (r.occurred_at, r.interaction_id)))

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        return {
            "known_id": scenarios.SESSION_COMPLETE,
            "expected_min_records": 2,
            "empty_id": scenarios.SESSION_NO_INTERACTIONS,
            "single_record_id": scenarios.SESSION_ONE_INTERACTION,
            "unavailable_id": scenarios.SESSION_INTERACTIONS_UNAVAILABLE,
            "timeout_id": scenarios.SESSION_TIMEOUT,
            "upstream_tokens": ("MockInteractionProvider", "scenarios."),
        }
