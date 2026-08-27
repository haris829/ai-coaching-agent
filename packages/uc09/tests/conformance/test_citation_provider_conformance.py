"""Contract conformance for every registered ``CitationProvider``.

The strongest assertion here is :meth:`test_every_resource_is_a_citation_event`.
A citation adapter that returns "authorities relevant to the topic" instead of
"authorities cited in the session" breaks the grounding guarantee at its
source, and no downstream check can detect it - downstream can only confirm the
summary matches what this port said.
"""

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
from uc09_summary.domain.enums import ResourceKind
from uc09_summary.domain.errors import ProviderTimeout, ProviderUnavailable
from uc09_summary.domain.models import Resource
from uc09_summary.ports import CitationProvider

PORT = "citation_provider"


@parametrized_over(PORT)
class TestCitationProviderContract:
    def _adapter(self, adapter_name: str):
        adapter = build_adapter(PORT, adapter_name)
        return adapter, profile_for(adapter, PORT, adapter_name)

    def test_satisfies_the_port_protocol(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert isinstance(adapter, CitationProvider)

    def test_exposes_no_mutating_method(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert_read_only_surface(adapter, allowed=("for_session",))

    def test_returns_platform_resources(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        records = adapter.for_session(require(profile, "known_id", PORT))

        assert isinstance(records, tuple)
        assert len(records) >= int(profile.get("expected_min_records", 1))
        for record in records:
            assert isinstance(record, Resource)
            assert isinstance(record.kind, ResourceKind)
            assert record.kind.value == record.kind.value.lower()
            assert record.citation.strip()
            assert record.title.strip()
            if record.first_cited_at is not None:
                assert_utc(record.first_cited_at, "first_cited_at")

    def test_every_resource_is_a_citation_event(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        records = adapter.for_session(profile["known_id"])
        for record in records:
            assert record.cited_in_interaction_ids, (
                f"Resource {record.resource_id!r} carries no interaction it was "
                "cited in, so it cannot be shown to have been cited during this "
                "session. This port returns citation events, not a reading "
                "list: an authority merely relevant to the topic must not be "
                "returned, because a CPD record that lists it becomes false."
            )

    def test_nothing_cited_returns_empty_not_an_error(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        assert adapter.for_session(require(profile, "empty_id", PORT)) == (), (
            "A session that cited nothing is a legitimate, reportable outcome."
        )

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
            what="the returned resources",
        )
        with pytest.raises(ProviderUnavailable) as caught:
            adapter.for_session(profile["unavailable_id"])
        assert_error_is_opaque(caught.value, tokens)
