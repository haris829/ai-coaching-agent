"""Provider selection: a registry lookup, loud on failure, never falling back."""

from __future__ import annotations

import pytest

from uc05.composition import (
    ADAPTER_MODULES,
    Container,
    load_adapter_modules,
    verify_configuration,
)
from uc05.config import load_settings
from uc05.domain.errors import UnknownProvider
from uc05.registry import (
    ANSWER_REGISTRY,
    CURRENT_USER_REGISTRY,
    DIALOGUE_REPOSITORY_REGISTRY,
    GUIDING_QUESTION_REGISTRY,
    INTENT_REGISTRY,
    INTERACTION_LOG_REPOSITORY_REGISTRY,
    LEARNER_CONTEXT_REGISTRY,
    REGISTRIES,
    SESSION_MODE_REPOSITORY_REGISTRY,
    ProviderRegistry,
)

ALL_REGISTRIES = (
    GUIDING_QUESTION_REGISTRY,
    ANSWER_REGISTRY,
    LEARNER_CONTEXT_REGISTRY,
    INTENT_REGISTRY,
    DIALOGUE_REPOSITORY_REGISTRY,
    SESSION_MODE_REPOSITORY_REGISTRY,
    INTERACTION_LOG_REPOSITORY_REGISTRY,
    CURRENT_USER_REGISTRY,
)


@pytest.fixture(autouse=True)
def _adapters_loaded():
    load_adapter_modules()


def test_every_port_has_a_registry():
    assert len(REGISTRIES) == len(ALL_REGISTRIES)
    for registry in ALL_REGISTRIES:
        assert registry.port_name in REGISTRIES


def test_every_registry_has_at_least_one_implementation():
    for registry in ALL_REGISTRIES:
        assert registry.keys(), registry.port_name


def test_registration_is_a_single_decorator():
    registry: ProviderRegistry = ProviderRegistry("demo_port", "DEMO", "DEMO_REGISTRY")

    @registry.register("demo")
    class _Demo:
        def __init__(self, **_):
            pass

    assert registry.keys() == ("demo",)
    assert isinstance(registry.create("demo"), _Demo)


def test_a_duplicate_key_is_refused():
    registry: ProviderRegistry = ProviderRegistry("demo_port2", "DEMO2", "DEMO2_REGISTRY")

    @registry.register("demo")
    class _First:
        def __init__(self, **_):
            pass

    with pytest.raises(UnknownProvider):

        @registry.register("demo")
        class _Second:
            def __init__(self, **_):
                pass


def test_an_unknown_key_fails_loudly_and_names_what_is_missing():
    with pytest.raises(UnknownProvider) as caught:
        LEARNER_CONTEXT_REGISTRY.create("company")

    message = str(caught.value)
    assert "company" in message
    assert "LEARNER_CONTEXT_PROVIDER=company" in message
    assert "LEARNER_CONTEXT_REGISTRY.register('company')" in message
    assert "uc05/adapters/real/company_learner_context_provider.py" in message
    assert "ADAPTER_MODULES" in message
    assert "_template.py" in message


def test_an_unknown_key_never_falls_back_to_a_mock():
    """The failure is total. There is no substitution, silent or otherwise."""
    with pytest.raises(UnknownProvider) as caught:
        LEARNER_CONTEXT_REGISTRY.create("company")
    assert "Refusing to start rather than falling back to a mock" in str(caught.value)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("GENERATOR", "not-a-real-generator"),
        ("LEARNER_CONTEXT_PROVIDER", "not-a-real-provider"),
        ("INTENT_CLASSIFIER", "not-a-real-classifier"),
        ("DIALOGUE_REPOSITORY", "not-a-real-repository"),
        ("SESSION_MODE_REPOSITORY", "not-a-real-repository"),
        ("INTERACTION_LOG_REPOSITORY", "not-a-real-repository"),
        ("CURRENT_USER_PROVIDER", "not-a-real-identity"),
    ],
)
def test_startup_verification_rejects_an_unregistered_provider(setting, value):
    settings = load_settings(**{setting: value})
    with pytest.raises(UnknownProvider) as caught:
        verify_configuration(settings)
    assert value in str(caught.value)


def test_startup_verification_accepts_the_default_configuration():
    verify_configuration(load_settings())


def test_a_container_refuses_to_build_on_an_unknown_provider():
    with pytest.raises(UnknownProvider):
        Container(load_settings(GENERATOR="nonexistent"))


def test_the_configured_generator_is_registered_but_disabled_by_default():
    assert "configured" in GUIDING_QUESTION_REGISTRY.keys()
    assert "configured" in ANSWER_REGISTRY.keys()
    assert load_settings().generator == "fake"


def test_selecting_the_configured_generator_without_settings_fails_loudly():
    from uc05.domain.errors import ProviderUnavailable

    with pytest.raises(ProviderUnavailable) as caught:
        Container(load_settings(GENERATOR="configured"))
    assert "GENERATOR_PROVIDER" in caught.value.detail
    assert "GENERATOR_API_KEY" in caught.value.detail


def test_adding_an_adapter_family_is_one_line_in_one_file():
    """The foreign family is registered by exactly one entry, nothing else."""
    assert "uc05.adapters.foreign" in ADAPTER_MODULES
    for key in ("acme",):
        assert key in LEARNER_CONTEXT_REGISTRY.keys()
        assert key in GUIDING_QUESTION_REGISTRY.keys()
        assert key in ANSWER_REGISTRY.keys()
        assert key in INTENT_REGISTRY.keys()
        assert LEARNER_CONTEXT_REGISTRY.origin_of(key) == "uc05.adapters.foreign.acme"


def test_the_template_is_not_importable_as_a_live_provider():
    """A half-wired skeleton must not be selectable."""
    assert "company" not in LEARNER_CONTEXT_REGISTRY.keys()


def test_provider_bindings_are_available_to_operators_only():
    container = Container(load_settings())
    described = container.describe()
    assert described["guiding_question_generator"] == "fake"
    assert described["learner_context_provider"] == "mock"
