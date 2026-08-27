"""Provider registry: table-driven selection, loud failure, no silent fallback."""

from __future__ import annotations

import pytest

from uc07.adapters.foreign.adapters import (
    ForeignCoursesProvider,
    ForeignFeedbackProvider,
    ForeignInteractionLogProvider,
    ForeignLearnerProfileProvider,
)
from uc07.adapters.mock.courses import MockCoursesProvider
from uc07.adapters.mock.feedback import MockFeedbackProvider
from uc07.adapters.mock.interaction_log import MockInteractionLogProvider
from uc07.adapters.mock.profile import MockLearnerProfileProvider
from uc07.application.config import Settings
from uc07.composition import (
    COURSES_PROVIDERS,
    FEEDBACK_PROVIDERS,
    INTERACTION_LOG_PROVIDERS,
    PROFILE_PROVIDERS,
    build_container,
    resolve,
)
from uc07.domain.errors import ConfigurationError

REGISTRIES = {
    "INTERACTION_LOG_PROVIDER": INTERACTION_LOG_PROVIDERS,
    "FEEDBACK_PROVIDER": FEEDBACK_PROVIDERS,
    "PROFILE_PROVIDER": PROFILE_PROVIDERS,
    "COURSES_PROVIDER": COURSES_PROVIDERS,
}


def settings(**overrides) -> Settings:
    base = {
        "interaction_log_provider": "mock",
        "feedback_provider": "mock",
        "profile_provider": "mock",
        "courses_provider": "mock",
        "mock_scenario": "struggle_mixed",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.parametrize("env_var", sorted(REGISTRIES))
def test_every_port_registry_offers_mock_and_foreign(env_var):
    assert {"mock", "foreign"} <= set(REGISTRIES[env_var])


@pytest.mark.parametrize("env_var", sorted(REGISTRIES))
def test_unknown_provider_name_fails_loudly(env_var):
    with pytest.raises(ConfigurationError) as excinfo:
        resolve(
            REGISTRIES[env_var],
            "company-that-is-not-registered",
            env_var=env_var,
            settings=settings(),
        )
    message = str(excinfo.value)
    assert env_var in message
    assert "not a registered provider" in message
    assert "never falls back" in message


@pytest.mark.parametrize("env_var", sorted(REGISTRIES))
def test_missing_real_provider_never_silently_falls_back_to_mock(env_var):
    field = env_var.lower()
    with pytest.raises(ConfigurationError):
        build_container(settings(**{field: "acme"}))


def test_mock_selection_wires_mock_adapters():
    container = build_container(settings())
    service = container.service
    assert isinstance(service._interactions, MockInteractionLogProvider)
    assert isinstance(service._feedback, MockFeedbackProvider)
    assert isinstance(service._profiles, MockLearnerProfileProvider)
    assert isinstance(service._courses, MockCoursesProvider)


def test_foreign_selection_wires_foreign_adapters_with_no_other_change():
    container = build_container(
        settings(
            interaction_log_provider="foreign",
            feedback_provider="foreign",
            profile_provider="foreign",
            courses_provider="foreign",
        )
    )
    service = container.service
    assert isinstance(service._interactions, ForeignInteractionLogProvider)
    assert isinstance(service._feedback, ForeignFeedbackProvider)
    assert isinstance(service._profiles, ForeignLearnerProfileProvider)
    assert isinstance(service._courses, ForeignCoursesProvider)


def test_ports_can_be_mixed_independently():
    container = build_container(settings(profile_provider="foreign"))
    assert isinstance(container.service._profiles, ForeignLearnerProfileProvider)
    assert isinstance(container.service._interactions, MockInteractionLogProvider)


def test_unknown_mock_scenario_fails_loudly():
    with pytest.raises(KeyError) as excinfo:
        build_container(settings(mock_scenario="scenario-that-does-not-exist"))
    assert "unknown mock scenario" in str(excinfo.value)


def test_thresholds_come_from_settings():
    container = build_container(settings())
    thresholds = container.settings.thresholds()
    assert thresholds.gap_report_threshold == 10
    assert thresholds.min_topic_areas == 3
    assert thresholds.explain_differently_struggle_threshold == 2
    assert thresholds.low_rating_struggle_threshold == 1
    assert thresholds.follow_up_struggle_threshold == 2


def test_registered_adapters_are_the_only_write_free_surface_used():
    container = build_container(settings())
    from uc07.ports.persistence import GapReportRepository

    assert isinstance(container.repository, GapReportRepository)
    assert not hasattr(container.service._interactions, "save")
    assert not hasattr(container.service._feedback, "save")
    assert not hasattr(container.service._profiles, "save")
    assert not hasattr(container.service._courses, "save")
