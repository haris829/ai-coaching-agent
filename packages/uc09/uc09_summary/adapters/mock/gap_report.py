"""Mock GapReportProvider. READ ONLY: retrieval method only.

Scenarios: suggestions available, none, unavailable.

``None`` and ``()`` are returned for genuinely different situations and the
service treats them differently. This component does not perform gap analysis;
it only consumes the result.
"""

from __future__ import annotations

from uc09_summary.adapters.mock import scenarios
from uc09_summary.domain.errors import ProviderTimeout, ProviderUnavailable
from uc09_summary.domain.models import Suggestion

PORT = "gap_report_provider"


class MockGapReportProvider:
    """In-process gap-report source covering the specified scenario matrix."""

    @classmethod
    def from_settings(cls, settings: object) -> MockGapReportProvider:
        return cls()

    def suggestions(self, user_id: str) -> tuple[Suggestion, ...] | None:
        """Return gap-report suggestions for the learner, or ``None`` if there is no report."""
        if user_id == scenarios.USER_GAP_UNAVAILABLE:
            raise ProviderUnavailable(PORT, "scenario_source_unavailable")
        if user_id == scenarios.USER_GAP_TIMEOUT:
            raise ProviderTimeout(PORT, "scenario_source_timeout")

        if user_id not in scenarios.GAP_SUGGESTIONS:
            # No gap report exists for this learner at all. Distinct from a
            # report that ran and suggested nothing.
            return None
        return tuple(scenarios.GAP_SUGGESTIONS[user_id])

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        return {
            "known_id": scenarios.OWNER_USER_ID,
            "expected_min_records": 1,
            "empty_id": scenarios.USER_NO_GAP_SUGGESTIONS,
            "none_id": "user-with-no-gap-report-at-all",
            "unavailable_id": scenarios.USER_GAP_UNAVAILABLE,
            "timeout_id": scenarios.USER_GAP_TIMEOUT,
            "upstream_tokens": ("MockGapReportProvider", "scenarios."),
        }
