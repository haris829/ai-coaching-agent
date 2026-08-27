"""Provider selection, startup failure, and the cost of adding a provider."""

from __future__ import annotations

import pytest

from uc10.adapters.registry import (
    INTERACTION_PROVIDERS,
    ProviderContext,
    UnknownProviderError,
    build_interaction_provider,
    register_interaction_provider,
)
from uc10.api.app import create_app
from uc10.api.deps import build_container, mint_dev_session_id


def test_provider_selection_is_a_single_registry_lookup():
    assert isinstance(INTERACTION_PROVIDERS, dict)
    assert set(INTERACTION_PROVIDERS) == {"mock", "foreign_demo"}


def test_an_unregistered_provider_fails_loudly_at_startup(settings):
    unknown = settings.model_copy(update={"interaction_provider": "company_lms"})
    with pytest.raises(UnknownProviderError) as raised:
        create_app(settings=unknown)

    message = str(raised.value)
    assert "company_lms" in message, "the missing key is named"
    assert "uc10/adapters/real/company_lms_interaction_provider.py" in message, (
        "the file expected to supply it is named"
    )
    assert "uc10/adapters/registry.py" in message, "the registry line's home is named"
    assert "_template.py" in message
    assert "mock" in message and "foreign_demo" in message, "the registered keys are listed"
    assert "will not fall back to a mock" in message


def test_there_is_no_silent_fallback_to_a_mock(settings, clock):
    unknown = settings.model_copy(update={"interaction_provider": "company_lms"})
    with pytest.raises(UnknownProviderError):
        build_container(settings=unknown, clock=clock)


def test_adding_a_provider_costs_one_registry_line_and_one_config_value(settings, clock):
    """The literal cost of an integration: a factory, one registry line, one env value."""

    class PretendCompanyProvider:  # the new adapter file
        def __init__(self, clock):
            self._clock = clock

        def get(self, interaction_id):  # pragma: no cover - selection is what is under test
            raise NotImplementedError

        def delivered_at(self, interaction_id):  # pragma: no cover
            raise NotImplementedError

    original = dict(INTERACTION_PROVIDERS)
    try:
        # ONE line, in one file:
        register_interaction_provider("company_lms", lambda ctx: PretendCompanyProvider(ctx.clock))
        # ONE config value:
        configured = settings.model_copy(update={"interaction_provider": "company_lms"})
        container = build_container(settings=configured, clock=clock)
        assert isinstance(container.interactions, PretendCompanyProvider)
        assert create_app(container=container) is not None
    finally:
        INTERACTION_PROVIDERS.clear()
        INTERACTION_PROVIDERS.update(original)


def test_the_registry_builds_each_known_provider(settings, clock):
    for key in INTERACTION_PROVIDERS:
        configured = settings.model_copy(update={"interaction_provider": key})
        provider = build_interaction_provider(
            ProviderContext(settings=configured, clock=clock)
        )
        assert hasattr(provider, "get") and hasattr(provider, "delivered_at")


# ------------------------------------------------------------ session identity


def test_this_component_never_mints_a_session_id_on_a_production_path(settings):
    with pytest.raises(RuntimeError) as raised:
        mint_dev_session_id(settings)
    assert "receives an opaque session_id" in str(raised.value)


def test_dev_session_minting_is_available_only_when_explicitly_enabled(settings):
    dev = settings.model_copy(update={"allow_dev_session_minting": True})
    minted = mint_dev_session_id(dev)
    assert minted.startswith("dev_sess_")


def test_session_minting_is_off_by_default(settings):
    assert settings.allow_dev_session_minting is False


def test_the_session_id_on_a_rating_is_the_one_the_platform_supplied(client, container):
    client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "up"},
        headers={"X-User-Id": "user_alice"},
    )
    record = container.ratings_repository.all_records()[0]
    assert record.session_id == container.interactions.get("int_answer").session_id
    assert not record.session_id.startswith("dev_sess_")


def test_healthz_reports_the_wiring_actually_in_use(client):
    wiring = client.get("/api/v1/healthz").json()["wiring"]
    assert wiring["interaction_provider"] == "mock"
    assert wiring["interaction_adapter"] == "MockInteractionProvider"
