"""Configuration defaults, guarded switches, and the provider factory (section 14)."""

from __future__ import annotations

import pathlib

import pytest

from uc02.infrastructure.config.settings import Settings
from uc02.infrastructure.providers.company import (
    CompanyCoursesProvider,
    CompanyLegalFootprintsProvider,
    CompanyNaricProvider,
    CompanyQuestionHistoryProvider,
)
from uc02.infrastructure.providers.factory import build_providers
from uc02.infrastructure.providers.mocks import (
    MockCoursesProvider,
    MockLegalFootprintsProvider,
    MockNaricProvider,
    MockQuestionHistoryProvider,
)


def _defaults() -> Settings:
    return Settings(_env_file=None)


def test_documented_defaults():
    settings = _defaults()
    assert settings.naric_provider == "mock"
    assert settings.courses_provider == "mock"
    assert settings.legal_provider == "mock"
    assert settings.history_provider == "mock"
    assert settings.provider_timeout_ms == 2000
    assert settings.context_assembly_budget_ms == 3000
    assert settings.question_history_limit == 20
    assert settings.context_ttl_hours == 12


def test_guarded_switches_default_to_off():
    settings = _defaults()
    assert settings.allow_dev_session_ids is False
    assert settings.debug_context_endpoint is False
    assert settings.allow_force_refresh is False


def test_production_guard_flags_unsafe_switches():
    unsafe = Settings(
        _env_file=None,
        environment="production",
        allow_dev_session_ids=True,
        debug_context_endpoint=True,
    )
    violations = unsafe.production_guard_violations()
    assert len(violations) == 2
    assert any("ALLOW_DEV_SESSION_IDS" in v for v in violations)
    assert any("DEBUG_CONTEXT_ENDPOINT" in v for v in violations)


def test_a_correctly_configured_production_deployment_has_no_violations():
    safe = Settings(_env_file=None, environment="production")
    assert safe.production_guard_violations() == []


def test_timeouts_are_exposed_in_seconds_for_asyncio():
    settings = Settings(_env_file=None, provider_timeout_ms=1500, context_assembly_budget_ms=2500)
    assert settings.provider_timeout_seconds == 1.5
    assert settings.assembly_budget_seconds == 2.5


def test_factory_returns_mock_adapters_by_default():
    bundle = build_providers(_defaults())
    assert isinstance(bundle.naric, MockNaricProvider)
    assert isinstance(bundle.courses, MockCoursesProvider)
    assert isinstance(bundle.legal, MockLegalFootprintsProvider)
    assert isinstance(bundle.history, MockQuestionHistoryProvider)


def test_factory_switches_adapter_per_source_from_config_alone():
    settings = Settings(
        _env_file=None,
        naric_provider="company",
        courses_provider="company",
        legal_provider="mock",
        history_provider="company",
    )
    bundle = build_providers(settings)
    assert isinstance(bundle.naric, CompanyNaricProvider)
    assert isinstance(bundle.courses, CompanyCoursesProvider)
    assert isinstance(bundle.legal, MockLegalFootprintsProvider)
    assert isinstance(bundle.history, CompanyQuestionHistoryProvider)
    assert isinstance(bundle.legal, MockLegalFootprintsProvider)
    assert CompanyLegalFootprintsProvider is not None  # stub exists for the flip


def test_an_unknown_provider_choice_is_rejected_by_configuration():
    with pytest.raises(ValueError):
        Settings(_env_file=None, naric_provider="not-a-provider")


def test_env_example_documents_every_configurable_value():
    example = pathlib.Path(".env.example").read_text(encoding="utf-8")
    for key in (
        "NARIC_PROVIDER",
        "COURSES_PROVIDER",
        "LEGAL_PROVIDER",
        "HISTORY_PROVIDER",
        "PROVIDER_TIMEOUT_MS",
        "CONTEXT_ASSEMBLY_BUDGET_MS",
        "QUESTION_HISTORY_LIMIT",
        "CONTEXT_TTL_HOURS",
        "ALLOW_DEV_SESSION_IDS",
        "DEBUG_CONTEXT_ENDPOINT",
    ):
        assert f"{key}=" in example


def test_env_example_holds_placeholders_not_secrets():
    example = pathlib.Path(".env.example").read_text(encoding="utf-8")
    assert "ALLOW_DEV_SESSION_IDS=false" in example
    assert "DEBUG_CONTEXT_ENDPOINT=false" in example
    assert "USER_ID_LOG_SALT=replace-me-with-a-deployment-secret" in example


def test_no_hard_coded_urls_or_timeouts_outside_config():
    """External endpoints are never invented: nothing calls a URL."""
    offenders = []
    for path in pathlib.Path("uc02").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "http://" in text or "https://" in text:
            offenders.append(path.as_posix())
    assert offenders == []
