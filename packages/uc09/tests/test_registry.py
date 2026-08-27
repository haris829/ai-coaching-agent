"""Provider selection is a registry lookup, and it fails loudly.

Two guarantees are tested here:

* adding a provider is one line in one file, and that line is enough - no
  other module learns the adapter exists;
* a configured provider with no implementation stops the process at startup,
  naming what is missing. It never falls back to a mock, because a service
  quietly running on fake data in production is worse than one that refuses to
  start.
"""

from __future__ import annotations

import pytest

from tests.conformance.kit import is_offline_testable
from uc09_summary.composition import PORTS, build_container
from uc09_summary.config import Settings, load_settings
from uc09_summary.domain.errors import ProviderNotRegistered, Uc09Error
from uc09_summary.registry import (
    EXPECTED_ADAPTER_LOCATION,
    REGISTRY,
    build_provider,
    env_var_for,
    load_implementation,
    registered_names,
    resolve_target,
)


class TestTheRegistryIsTheOnlySelectionMechanism:
    def test_every_port_has_a_registry_entry(self) -> None:
        assert set(PORTS) == set(REGISTRY)

    def test_every_settings_field_matches_a_port(self) -> None:
        for port in PORTS:
            assert hasattr(Settings(), port)

    def test_every_registered_target_resolves(self) -> None:
        for port, implementations in REGISTRY.items():
            for name in implementations:
                assert load_implementation(port, name) is not None

    def test_every_registered_implementation_exposes_the_construction_hook(self) -> None:
        for port, implementations in REGISTRY.items():
            for name in implementations:
                implementation = load_implementation(port, name)
                assert hasattr(implementation, "from_settings"), (
                    f"{implementation.__qualname__} must expose from_settings; "
                    "that is the whole construction contract."
                )

    def test_every_offline_implementation_constructs_from_default_settings(self) -> None:
        settings = load_settings()
        for port, implementations in REGISTRY.items():
            for name in implementations:
                if not is_offline_testable(port, name):
                    continue
                assert load_implementation(port, name).from_settings(settings) is not None

    def test_an_implementation_needing_configuration_refuses_without_it(self) -> None:
        """No silent start on an unconfigured real provider.

        A generation service that quietly does nothing would produce
        question-log fallbacks indistinguishable from an upstream outage, so
        the adapter refuses to be built at all.
        """
        settings = load_settings(upstream_base_url="")
        for port, implementations in REGISTRY.items():
            for name in implementations:
                if is_offline_testable(port, name):
                    continue
                implementation = load_implementation(port, name)
                with pytest.raises(Uc09Error):
                    implementation.from_settings(settings)

    def test_an_implementation_outside_the_offline_suite_says_what_it_needs(self) -> None:
        """An adapter may opt out of offline contract testing, but not silently."""
        for port, implementations in REGISTRY.items():
            for name in implementations:
                if is_offline_testable(port, name):
                    continue
                profile = load_implementation(port, name).conformance_profile()
                assert profile.get("requires"), (
                    f"{port}:{name} declares offline=False but does not name the "
                    "configuration the conformance suite needs in order to run "
                    "against it. Add a 'requires' entry, so that an adapter "
                    "outside the default suite is documented rather than merely "
                    "absent."
                )

    def test_the_real_generator_is_registered_but_not_the_default(self) -> None:
        assert "http" in REGISTRY["summary_generator"]
        assert Settings().summary_generator == "fake", (
            "The deterministic generator is the default, so the whole suite "
            "runs with no API key and no network."
        )

    def test_no_conditional_provider_selection_exists_in_the_codebase(self) -> None:
        """The rule is a registry, not a chain of conditionals."""
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[1] / "uc09_summary"
        pattern = re.compile(
            r'if\s+\w*(setting|provider|adapter)\w*\s*==\s*["\']', re.IGNORECASE
        )
        offenders = [
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], (
            f"Conditional provider selection found in {offenders}. Selection "
            "is a single registry lookup so that adding a provider is one line."
        )

    def test_adding_a_provider_touches_only_the_registry(self) -> None:
        """Register a new adapter at runtime and use it, changing nothing else."""
        from tests.support.spare_adapter import SpareSessionProvider

        REGISTRY["session_provider"]["spare"] = (
            "tests.support.spare_adapter:SpareSessionProvider"
        )
        try:
            container = build_container(load_settings(session_provider="spare"))
            assert isinstance(container.providers["session_provider"], SpareSessionProvider)

            record = container.providers["session_provider"].get_session("spare-1")
            assert record.session_id == "spare-1"
        finally:
            del REGISTRY["session_provider"]["spare"]


class TestUnknownProviderFailsLoudly:
    def test_an_unregistered_name_raises_at_startup(self) -> None:
        with pytest.raises(ProviderNotRegistered):
            build_container(load_settings(session_provider="company"))

    def test_the_failure_names_the_missing_key(self) -> None:
        with pytest.raises(ProviderNotRegistered) as caught:
            resolve_target("session_provider", "company")

        message = str(caught.value)
        assert "'company'" in message
        assert "session_provider" in message

    def test_the_failure_names_the_environment_variable(self) -> None:
        with pytest.raises(ProviderNotRegistered) as caught:
            resolve_target("citation_provider", "vendor")

        assert "UC09_CITATION_PROVIDER" in str(caught.value)
        assert env_var_for("citation_provider") == "UC09_CITATION_PROVIDER"

    def test_the_failure_names_the_file_expected_to_supply_it(self) -> None:
        with pytest.raises(ProviderNotRegistered) as caught:
            resolve_target("gap_report_provider", "vendor")

        assert EXPECTED_ADAPTER_LOCATION["gap_report_provider"] in str(caught.value)
        assert "uc09_summary/registry.py" in str(caught.value)

    def test_the_failure_lists_the_names_that_do_exist(self) -> None:
        with pytest.raises(ProviderNotRegistered) as caught:
            resolve_target("session_provider", "typo")

        for name in registered_names("session_provider"):
            assert name in str(caught.value)

    def test_there_is_no_silent_fallback_to_a_mock(self) -> None:
        with pytest.raises(ProviderNotRegistered) as caught:
            build_container(load_settings(summary_generator="openai"))

        assert "refused rather than falling back to a mock" in str(caught.value)

    def test_an_unknown_port_is_rejected(self) -> None:
        with pytest.raises(ProviderNotRegistered):
            resolve_target("not_a_port", "mock")

    def test_a_broken_dotted_path_reports_the_path(self) -> None:
        REGISTRY["clock"]["broken"] = "uc09_summary.does.not.exist:Thing"
        try:
            with pytest.raises(ProviderNotRegistered) as caught:
                load_implementation("clock", "broken")
            assert "uc09_summary.does.not.exist" in str(caught.value)
        finally:
            del REGISTRY["clock"]["broken"]

    def test_a_missing_attribute_reports_the_attribute(self) -> None:
        REGISTRY["clock"]["missing"] = "uc09_summary.adapters.real.clock:NoSuchClock"
        try:
            with pytest.raises(ProviderNotRegistered) as caught:
                load_implementation("clock", "missing")
            assert "NoSuchClock" in str(caught.value)
        finally:
            del REGISTRY["clock"]["missing"]

    def test_an_implementation_without_from_settings_is_rejected(self) -> None:
        REGISTRY["clock"]["bad"] = "tests.support.spare_adapter:ClockWithoutFactory"
        try:
            with pytest.raises(ProviderNotRegistered) as caught:
                build_provider("clock", load_settings(clock="bad"))
            assert "from_settings" in str(caught.value)
            assert "_template.py" in str(caught.value)
        finally:
            del REGISTRY["clock"]["bad"]


class TestStartupIsEagerAndStrict:
    def test_every_provider_is_built_before_a_request_is_served(self) -> None:
        container = build_container(load_settings())
        assert set(container.providers) == set(PORTS)

    def test_a_bad_configuration_fails_before_the_app_exists(self) -> None:
        from uc09_summary.api.app import create_app

        with pytest.raises(ProviderNotRegistered):
            create_app(settings=load_settings(document_renderer="wkhtmltopdf"))

    def test_healthz_reports_the_configured_provider_names(self) -> None:
        from tests.support.harness import build_harness

        harness = build_harness()
        body = harness.client.get("/api/v1/healthz").json()

        assert body["status"] == "ok"
        assert body["providers"]["summary_generator"] == "fake"
        assert set(body["providers"]) == set(PORTS)


class TestTheAdapterTemplate:
    def test_the_template_exists(self) -> None:
        import pathlib

        template = (
            pathlib.Path(__file__).resolve().parents[1]
            / "uc09_summary/adapters/real/_template.py"
        )
        assert template.exists()

    def test_the_template_marks_every_point_needing_a_real_value(self) -> None:
        import pathlib

        text = (
            pathlib.Path(__file__).resolve().parents[1]
            / "uc09_summary/adapters/real/_template.py"
        ).read_text(encoding="utf-8")

        for marker in (
            "TODO(1) ENDPOINT",
            "TODO(2) VALUE MAPPINGS",
            "TODO(3) AUTH",
            "TODO(4) ERROR TRANSLATION",
            "TODO(5) PAYLOAD MAPPING",
        ):
            assert marker in text, f"The template must mark {marker}."

    def test_the_template_provides_the_construction_and_conformance_hooks(self) -> None:
        import pathlib

        text = (
            pathlib.Path(__file__).resolve().parents[1]
            / "uc09_summary/adapters/real/_template.py"
        ).read_text(encoding="utf-8")

        assert "def from_settings(" in text
        assert "def conformance_profile(" in text

    def test_the_template_is_importable_so_it_cannot_rot(self) -> None:
        import importlib

        module = importlib.import_module("uc09_summary.adapters.real._template")
        assert hasattr(module, "TemplateSessionProvider")

    def test_the_template_is_not_registered(self) -> None:
        """A skeleton must never be selectable as a real provider."""
        for implementations in REGISTRY.values():
            for target in implementations.values():
                assert "_template" not in target
