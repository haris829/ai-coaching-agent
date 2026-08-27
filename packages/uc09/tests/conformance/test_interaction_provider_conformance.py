"""Contract conformance for every registered ``InteractionProvider``."""

from __future__ import annotations

import pytest

from tests.conformance.kit import (
    assert_error_is_opaque,
    assert_no_upstream_leak,
    assert_read_only_surface,
    assert_utc,
    build_adapter,
    parametrized_over,
    profile_for,
    require,
)
from uc09_summary.domain.errors import ProviderTimeout, ProviderUnavailable
from uc09_summary.domain.models import InteractionRecord
from uc09_summary.ports import InteractionProvider

PORT = "interaction_provider"


@parametrized_over(PORT)
class TestInteractionProviderContract:
    def _adapter(self, adapter_name: str):
        adapter = build_adapter(PORT, adapter_name)
        return adapter, profile_for(adapter, PORT, adapter_name)

    def test_satisfies_the_port_protocol(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert isinstance(adapter, InteractionProvider)

    def test_exposes_no_mutating_method(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert_read_only_surface(adapter, allowed=("for_session",))

    def test_returns_platform_interaction_records(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        records = adapter.for_session(require(profile, "known_id", PORT))

        assert isinstance(records, tuple)
        assert len(records) >= int(profile.get("expected_min_records", 1))
        for record in records:
            assert isinstance(record, InteractionRecord)
            assert record.session_id == profile["known_id"]
            assert_utc(record.occurred_at, "occurred_at")
            assert isinstance(record.topic_tags, tuple)
            assert isinstance(record.concept_tags, tuple)

    def test_records_are_ordered_oldest_first(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        records = adapter.for_session(profile["known_id"])
        stamps = [r.occurred_at for r in records]
        assert stamps == sorted(stamps)

    def test_tags_are_lowercase_platform_form(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        records = adapter.for_session(profile["known_id"])
        for record in records:
            for tag in record.topic_tags + record.concept_tags:
                assert tag == tag.lower(), (
                    "Tag vocabularies are normalised inside the adapter. "
                    "Grounding compares tags to tags and must compare like "
                    "with like."
                )
                assert "_" not in tag, "Platform tag form is kebab-case."

    def test_no_interactions_returns_empty_not_an_error(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        records = adapter.for_session(require(profile, "empty_id", PORT))
        assert records == (), (
            "An empty session is a legitimate state and must not be signalled "
            "as a failure: empty and unavailable are different facts."
        )

    def test_single_interaction_session_is_supported(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        records = adapter.for_session(require(profile, "single_record_id", PORT))
        assert len(records) == 1

    def test_unavailable_upstream_raises_provider_unavailable(
        self, adapter_name: str
    ) -> None:
        adapter, profile = self._adapter(adapter_name)
        with pytest.raises(ProviderUnavailable) as caught:
            adapter.for_session(require(profile, "unavailable_id", PORT))
        assert caught.value.port == PORT

    def test_slow_upstream_raises_provider_timeout(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        with pytest.raises(ProviderTimeout):
            adapter.for_session(require(profile, "timeout_id", PORT))

    def test_no_upstream_detail_escapes(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        tokens = tuple(profile.get("upstream_tokens", ()))
        assert_no_upstream_leak(
            adapter.for_session(profile["known_id"]),
            tokens,
            what="the returned interaction records",
        )
        with pytest.raises(ProviderUnavailable) as caught:
            adapter.for_session(profile["unavailable_id"])
        assert_error_is_opaque(caught.value, tokens)
