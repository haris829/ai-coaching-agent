"""Mock SessionProvider. READ ONLY: retrieval method only, no mutating method.

Scenarios, selected by session id:

============================== ===============================================
``sess-complete-multi-topic``  complete session, several topics
``sess-in-progress``           in progress, no end time
``sess-not-owned``             belongs to a different learner
``sess-session-provider-down`` unavailable
``sess-session-provider-timeout`` timeout
``sess-does-not-exist``        reachable, no such session
``sess-invalid-naric``         upstream sent an unmappable level
============================== ===============================================
"""

from __future__ import annotations

from uc09_summary.adapters.mock import scenarios
from uc09_summary.domain.errors import (
    ProviderTimeout,
    ProviderUnavailable,
    SessionNotFound,
)
from uc09_summary.domain.models import SessionRecord

PORT = "session_provider"


class MockSessionProvider:
    """In-process session source covering the specified scenario matrix."""

    @classmethod
    def from_settings(cls, settings: object) -> MockSessionProvider:
        return cls()

    def get_session(self, session_id: str) -> SessionRecord:
        """Return the scenario session for ``session_id``."""
        if session_id == scenarios.SESSION_UNAVAILABLE:
            raise ProviderUnavailable(PORT, "scenario_source_unavailable")
        if session_id == scenarios.SESSION_TIMEOUT:
            raise ProviderTimeout(PORT, "scenario_source_timeout")

        record = scenarios.SESSIONS.get(session_id)
        if record is None:
            raise SessionNotFound(session_id)
        return record

    # Conformance support ---------------------------------------------------

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        """Scenario handles the adapter-agnostic conformance suite drives."""
        return {
            "known_id": scenarios.SESSION_COMPLETE,
            "expected_user_id": scenarios.OWNER_USER_ID,
            "missing_id": scenarios.SESSION_MISSING,
            "unavailable_id": scenarios.SESSION_UNAVAILABLE,
            "timeout_id": scenarios.SESSION_TIMEOUT,
            "invalid_naric_id": scenarios.SESSION_INVALID_NARIC,
            "upstream_tokens": ("MockSessionProvider", "scenarios."),
        }
