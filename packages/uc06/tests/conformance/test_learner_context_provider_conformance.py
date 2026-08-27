"""LearnerContextProvider conformance. Adapter-agnostic.

The load-bearing assertion here is normalisation: a NARIC level must arrive as
the platform enum regardless of what the upstream sent, and a value that maps to
no member must raise ProviderInvalidResponse rather than being rounded to a
neighbour or quietly defaulted.
"""

from __future__ import annotations

import inspect

import pytest

from uc06.domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from uc06.domain.errors import ProviderError, ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc06.domain.models import LearnerContext
from uc06.ports.learner_context import LearnerContextProvider

from .conftest import scenario

ACTOR = "conformance-user"

LEAK_MARKERS = ("envelope", "eqfBand", "bandOrigin", "specialism", "TODO_", "MatterSphere", "UPSTREAM_")


class TestShape:
    def test_it_satisfies_the_port(self, learner_context_adapter, context_scenarios):
        assert isinstance(learner_context_adapter, LearnerContextProvider)

    def test_the_signature_matches_the_port(self, learner_context_adapter, context_scenarios):
        assert list(inspect.signature(learner_context_adapter.get_context).parameters) == [
            "session_id",
            "user_id",
        ]


class TestNormalisation:
    def test_it_returns_the_platform_type(self, learner_context_adapter, context_scenarios):
        context = learner_context_adapter.get_context(scenario(context_scenarios, "available"), ACTOR)
        assert isinstance(context, LearnerContext)

    def test_the_naric_level_arrives_as_the_platform_enum(self, learner_context_adapter, context_scenarios):
        context = learner_context_adapter.get_context(scenario(context_scenarios, "available"), ACTOR)
        assert isinstance(context.naric_level, NaricLevel)
        assert context.naric_level in set(NaricLevel)

    def test_the_level_source_arrives_as_the_platform_enum(self, learner_context_adapter, context_scenarios):
        context = learner_context_adapter.get_context(scenario(context_scenarios, "available"), ACTOR)
        assert isinstance(context.naric_level_source, NaricLevelSource)

    def test_the_source_status_arrives_as_the_platform_enum(self, learner_context_adapter, context_scenarios):
        context = learner_context_adapter.get_context(scenario(context_scenarios, "available"), ACTOR)
        assert isinstance(context.source_status, SourceStatus)

    def test_the_session_and_user_are_echoed_unchanged(self, learner_context_adapter, context_scenarios):
        session_id = scenario(context_scenarios, "available")
        context = learner_context_adapter.get_context(session_id, ACTOR)
        assert context.session_id == session_id
        assert context.user_id == ACTOR

    def test_a_missing_practice_area_is_none_not_a_guess(self, learner_context_adapter, context_scenarios):
        """An adapter never invents data. Absent means None."""
        session_id = scenario(context_scenarios, "no_practice_area")
        assert learner_context_adapter.get_context(session_id, ACTOR).practice_area is None


class TestFailureModes:
    def test_an_unmappable_level_is_invalid_not_a_nearest_neighbour(
        self, learner_context_adapter, context_scenarios
    ):
        session_id = scenario(context_scenarios, "unmappable_level")
        with pytest.raises(ProviderInvalidResponse):
            learner_context_adapter.get_context(session_id, ACTOR)

    def test_an_unreachable_upstream_raises_provider_unavailable(
        self, learner_context_adapter, context_scenarios
    ):
        with pytest.raises(ProviderUnavailable):
            learner_context_adapter.get_context(scenario(context_scenarios, "unavailable"), ACTOR)

    def test_a_slow_upstream_raises_provider_timeout(self, learner_context_adapter, context_scenarios):
        with pytest.raises(ProviderTimeout):
            learner_context_adapter.get_context(scenario(context_scenarios, "timeout"), ACTOR)

    def test_no_other_exception_type_escapes(self, learner_context_adapter, context_scenarios):
        for key in ("unavailable", "timeout", "unmappable_level"):
            session_id = scenario(context_scenarios, key)
            try:
                learner_context_adapter.get_context(session_id, ACTOR)
            except ProviderError:
                pass
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"{key}: uncontracted exception escaped: {type(exc).__name__}")


class TestBoundaryHygiene:
    def test_no_upstream_detail_escapes_in_the_context(self, learner_context_adapter, context_scenarios):
        blob = repr(learner_context_adapter.get_context(scenario(context_scenarios, "available"), ACTOR))
        for marker in LEAK_MARKERS:
            assert marker not in blob

    def test_no_upstream_detail_escapes_in_an_exception(self, learner_context_adapter, context_scenarios):
        for key in ("unavailable", "timeout", "unmappable_level"):
            session_id = scenario(context_scenarios, key)
            try:
                learner_context_adapter.get_context(session_id, ACTOR)
            except ProviderError as exc:
                message = str(exc)
                for marker in LEAK_MARKERS:
                    assert marker not in message
                assert type(learner_context_adapter).__name__ not in message
                assert exc.port == "learner_context_provider"
