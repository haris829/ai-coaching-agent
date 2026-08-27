"""Contract conformance for every registered ``GapReportProvider``.

The distinction this suite exists to protect: ``None`` (no gap report at all)
and ``()`` (a report that suggested nothing) are different facts, and the
summary reports them differently. An adapter that collapses one into the other
makes the summary state something untrue about why Next Steps is short.
"""

from __future__ import annotations

import pytest

from tests.conformance.kit import (
    assert_error_is_opaque,
    assert_no_upstream_leak,
    assert_read_only_surface,
    build_adapter,
    parametrized_over,
    profile_for,
    require,
)
from uc09_summary.domain.enums import SuggestionSource
from uc09_summary.domain.errors import ProviderTimeout, ProviderUnavailable
from uc09_summary.domain.models import Suggestion
from uc09_summary.ports import GapReportProvider

PORT = "gap_report_provider"


@parametrized_over(PORT)
class TestGapReportProviderContract:
    def _adapter(self, adapter_name: str):
        adapter = build_adapter(PORT, adapter_name)
        return adapter, profile_for(adapter, PORT, adapter_name)

    def test_satisfies_the_port_protocol(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert isinstance(adapter, GapReportProvider)

    def test_exposes_no_mutating_method(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert_read_only_surface(adapter, allowed=("suggestions",))

    def test_returns_platform_suggestions(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        result = adapter.suggestions(require(profile, "known_id", PORT))

        assert result is not None
        assert len(result) >= int(profile.get("expected_min_records", 1))
        for suggestion in result:
            assert isinstance(suggestion, Suggestion)
            assert suggestion.source is SuggestionSource.GAP_REPORT, (
                "A suggestion from this port carries gap_report provenance. "
                "Provenance is what lets the grounding check confirm the "
                "suggestion was not invented."
            )
            assert suggestion.suggestion_id.strip()
            assert suggestion.label.strip()

    def test_report_with_no_suggestions_returns_empty_tuple(
        self, adapter_name: str
    ) -> None:
        adapter, profile = self._adapter(adapter_name)
        assert adapter.suggestions(require(profile, "empty_id", PORT)) == ()

    def test_absent_report_returns_none_not_empty(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        assert adapter.suggestions(require(profile, "none_id", PORT)) is None, (
            "No gap report at all must be None, not (). The two are different "
            "states in the platform vocabulary - unavailable versus empty - "
            "and the summary reports them differently."
        )

    def test_unavailable_upstream_raises_provider_unavailable(
        self, adapter_name: str
    ) -> None:
        adapter, profile = self._adapter(adapter_name)
        with pytest.raises(ProviderUnavailable) as caught:
            adapter.suggestions(require(profile, "unavailable_id", PORT))
        assert caught.value.port == PORT

    def test_slow_upstream_raises_provider_timeout(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        with pytest.raises(ProviderTimeout):
            adapter.suggestions(require(profile, "timeout_id", PORT))

    def test_no_upstream_detail_escapes(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        tokens = tuple(profile.get("upstream_tokens", ()))
        assert_no_upstream_leak(
            adapter.suggestions(profile["known_id"]),
            tokens,
            what="the returned suggestions",
        )
        with pytest.raises(ProviderUnavailable) as caught:
            adapter.suggestions(profile["unavailable_id"])
        assert_error_is_opaque(caught.value, tokens)
