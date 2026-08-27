"""Conformance suite for the ``LearnerContextProvider`` port."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ..domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ..domain.errors import ProviderError, ProviderUnavailable
from ..domain.models import LearnerContext
from ._shared import assert_no_upstream_leakage


@dataclass(frozen=True)
class LearnerContextScenarios:
    session_id: str
    #: A learner the upstream holds a real qualification level for.
    known_user_id: str
    #: A learner the upstream holds nothing for.
    empty_user_id: str | None = None
    #: A learner whose upstream value maps to no enum member.
    invalid_level_user_id: str | None = None
    #: A learner whose lookup fails outright.
    unavailable_user_id: str | None = None


class LearnerContextConformance:
    @pytest.fixture
    def adapter(self):  # pragma: no cover - overridden by the implementer
        raise NotImplementedError("provide an `adapter` fixture")

    @pytest.fixture
    def scenarios(self) -> LearnerContextScenarios:  # pragma: no cover - overridden
        raise NotImplementedError("provide a `scenarios` fixture")

    def test_returns_domain_model(self, adapter, scenarios) -> None:
        context = adapter.get_context(scenarios.session_id, scenarios.known_user_id)
        assert isinstance(context, LearnerContext)
        assert context.user_id == scenarios.known_user_id

    def test_level_is_the_platform_enum(self, adapter, scenarios) -> None:
        """However the upstream expresses attainment, what leaves the adapter is the enum."""
        context = adapter.get_context(scenarios.session_id, scenarios.known_user_id)
        assert isinstance(context.naric_level, NaricLevel)

    def test_retrieved_level_is_marked_retrieved(self, adapter, scenarios) -> None:
        context = adapter.get_context(scenarios.session_id, scenarios.known_user_id)
        assert context.naric_level_source is NaricLevelSource.RETRIEVED

    def test_empty_context_defaults_and_is_marked_default(self, adapter, scenarios) -> None:
        if scenarios.empty_user_id is None:
            pytest.skip("no empty-context scenario for this adapter")
        context = adapter.get_context(scenarios.session_id, scenarios.empty_user_id)
        assert context.naric_level is NaricLevel.LEVEL_5
        assert context.naric_level_source is NaricLevelSource.DEFAULT, (
            "a fallback must never be presented as retrieved"
        )
        assert context.source_status in (SourceStatus.EMPTY, SourceStatus.UNAVAILABLE)

    def test_unmappable_level_is_invalid_not_a_guess(self, adapter, scenarios) -> None:
        if scenarios.invalid_level_user_id is None:
            pytest.skip("no invalid-level scenario for this adapter")
        context = adapter.get_context(scenarios.session_id, scenarios.invalid_level_user_id)
        assert context.naric_level is NaricLevel.LEVEL_5
        assert context.naric_level_source is NaricLevelSource.DEFAULT
        assert context.source_status is SourceStatus.INVALID, (
            "a value that maps to no enum member is an invalid response, not a level"
        )

    def test_unavailable_raises_the_contract_exception(self, adapter, scenarios) -> None:
        if scenarios.unavailable_user_id is None:
            pytest.skip("no unavailable scenario for this adapter")
        with pytest.raises(ProviderUnavailable) as exc:
            adapter.get_context(scenarios.session_id, scenarios.unavailable_user_id)
        assert_no_upstream_leakage(exc.value)

    def test_no_raw_exception_escapes(self, adapter, scenarios) -> None:
        if scenarios.unavailable_user_id is None:
            pytest.skip("no failure scenario for this adapter")
        try:
            adapter.get_context(scenarios.session_id, scenarios.unavailable_user_id)
        except ProviderError:
            return
        except Exception as exc:  # noqa: BLE001 - this is exactly what is being checked
            raise AssertionError(f"{type(exc).__name__} escaped the adapter boundary") from exc
