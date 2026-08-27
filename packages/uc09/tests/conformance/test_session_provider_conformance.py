"""Contract conformance for every registered ``SessionProvider``.

Adapter-agnostic. Parameterised from the registry, so a real adapter is covered
the moment its one registry line exists - no new test is written to validate it.

    pytest tests/conformance/test_session_provider_conformance.py
    UC09_CONFORMANCE_ONLY=company pytest tests/conformance
"""

from __future__ import annotations

import pytest

from tests.conformance.kit import (
    assert_error_is_opaque,
    assert_no_upstream_leak,
    assert_platform_naric,
    assert_read_only_surface,
    assert_utc,
    build_adapter,
    parametrized_over,
    profile_for,
    require,
)
from uc09_summary.domain.enums import (
    NaricLevel,
    NaricLevelSource,
    SessionStatus,
    SourceStatus,
)
from uc09_summary.domain.errors import (
    ProviderTimeout,
    ProviderUnavailable,
    SessionNotFound,
)
from uc09_summary.domain.models import SessionRecord
from uc09_summary.ports import SessionProvider

PORT = "session_provider"


@parametrized_over(PORT)
class TestSessionProviderContract:
    """Behaviour every session adapter must exhibit, whatever it talks to."""

    def _adapter(self, adapter_name: str):
        adapter = build_adapter(PORT, adapter_name)
        return adapter, profile_for(adapter, PORT, adapter_name)

    def test_satisfies_the_port_protocol(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert isinstance(adapter, SessionProvider)

    def test_exposes_no_mutating_method(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert_read_only_surface(adapter, allowed=("get_session",))

    def test_returns_a_platform_session_record(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        record = adapter.get_session(require(profile, "known_id", PORT))

        assert isinstance(record, SessionRecord)
        assert record.session_id == profile["known_id"], (
            "The session identifier is opaque and is echoed back unchanged; an "
            "adapter must not re-mint or rewrite it."
        )
        assert record.user_id == require(profile, "expected_user_id", PORT)
        assert record.user_display_name
        assert isinstance(record.status, SessionStatus)
        assert_utc(record.started_at, "started_at")
        if record.ended_at is not None:
            assert_utc(record.ended_at, "ended_at")

    def test_normalises_naric_level_to_the_platform_enum(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        record = adapter.get_session(require(profile, "known_id", PORT))
        assert_platform_naric(record)

    def test_course_completion_is_an_integer_percentage(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        record = adapter.get_session(require(profile, "known_id", PORT))

        assert isinstance(record.course_completion_percent, int)
        assert not isinstance(record.course_completion_percent, bool)
        assert 0 <= record.course_completion_percent <= 100, (
            "Completion is an integer 0-100 on the platform contract. A 0..1 "
            "ratio must be converted inside the adapter."
        )

    def test_unmappable_level_becomes_the_default_marked_invalid(
        self, adapter_name: str
    ) -> None:
        adapter, profile = self._adapter(adapter_name)
        record = adapter.get_session(require(profile, "invalid_naric_id", PORT))

        assert record.naric_level is NaricLevel.LEVEL_5
        assert record.naric_level_source is NaricLevelSource.DEFAULT
        assert record.naric_level_status is SourceStatus.INVALID, (
            "A value mapping to no enum member is an invalid response, not a "
            "level. It must be recorded as invalid, never silently defaulted "
            "and never rounded to a nearby level."
        )

    def test_missing_session_raises_session_not_found(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        with pytest.raises(SessionNotFound):
            adapter.get_session(require(profile, "missing_id", PORT))

    def test_unavailable_upstream_raises_provider_unavailable(
        self, adapter_name: str
    ) -> None:
        adapter, profile = self._adapter(adapter_name)
        with pytest.raises(ProviderUnavailable) as caught:
            adapter.get_session(require(profile, "unavailable_id", PORT))
        assert caught.value.port == PORT

    def test_slow_upstream_raises_provider_timeout(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        with pytest.raises(ProviderTimeout) as caught:
            adapter.get_session(require(profile, "timeout_id", PORT))
        assert caught.value.port == PORT

    def test_records_carry_no_upstream_detail(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        tokens = tuple(profile.get("upstream_tokens", ()))
        record = adapter.get_session(profile["known_id"])
        assert_no_upstream_leak(record, tokens, what="the returned SessionRecord")

    def test_errors_carry_no_upstream_detail(self, adapter_name: str) -> None:
        adapter, profile = self._adapter(adapter_name)
        tokens = tuple(profile.get("upstream_tokens", ()))

        for key, expected in (
            ("unavailable_id", ProviderUnavailable),
            ("timeout_id", ProviderTimeout),
        ):
            with pytest.raises(expected) as caught:
                adapter.get_session(profile[key])
            assert_error_is_opaque(caught.value, tokens)
