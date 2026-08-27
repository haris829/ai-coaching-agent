"""Provider selection: a registry lookup that fails loudly and never falls back."""

from __future__ import annotations

import pytest

from uc06.composition import PROVIDER_REGISTRY, REGISTRY, build_container, registered_names
from uc06.config import PROVIDER_KEYS, Settings
from uc06.domain.errors import ConfigurationError
from uc06.registry import ADAPTER_TEMPLATE, REGISTRY_FILE, ProviderRegistry

from .conftest import make_settings


class TestSelectionIsALookup:
    def test_every_port_has_at_least_one_registered_provider(self):
        for _, port_key in PROVIDER_KEYS:
            assert registered_names(port_key), f"{port_key} has no registered provider"

    def test_a_provider_is_one_line_and_needs_no_import(self):
        """Entries are dotted paths, resolved lazily, so registering an adapter
        adds one line and no import statement."""
        for port, entries in PROVIDER_REGISTRY.items():
            for name, entry in entries.items():
                assert isinstance(entry, str), f"{port}/{name} is not a dotted path"
                assert ":" in entry

    def test_adding_a_provider_touches_only_the_registry(self):
        """A registry built from a dict with one extra line resolves the new
        adapter with no other change anywhere."""
        table = {port: dict(entries) for port, entries in PROVIDER_REGISTRY.items()}
        table["case_file_provider"]["newco"] = "uc06.adapters.foreign.case_file:ForeignCaseFileAdapter"

        resolved = ProviderRegistry(table).resolve("case_file_provider", "newco", Settings())
        assert hasattr(resolved, "get_case_file")

    def test_the_composition_root_is_the_only_registry(self):
        """One table, defined once. Other modules may NAME it in an error message
        or a comment - they must not define a second one."""
        import re
        from pathlib import Path

        definition = re.compile(r"^PROVIDER_REGISTRY\s*[:=]", re.MULTILINE)
        offenders = [
            str(path)
            for path in Path("uc06").rglob("*.py")
            if definition.search(path.read_text(encoding="utf-8"))
            and path != Path("uc06/composition.py")
        ]
        assert offenders == []

    def test_no_provider_selection_is_a_chain_of_conditionals(self):
        from pathlib import Path

        source = Path("uc06/composition.py").read_text(encoding="utf-8")
        assert 'elif' not in source
        assert '== "mock"' not in source
        assert '== "fake"' not in source


class TestUnknownProvidersFailLoudly:
    @pytest.mark.parametrize(
        "attribute,value",
        [
            ("case_file_provider", "company"),
            ("learner_context_provider", "company"),
            ("answer_generator", "acme"),
            ("guard_classifier", "acme"),
            ("interaction_log_repository", "postgres"),
            ("session_halt_repository", "redis"),
            ("admin_alert_sink", "pagerduty"),
            ("security_incident_sink", "siem"),
            ("current_user_provider", "oidc"),
        ],
    )
    def test_startup_fails_naming_what_is_missing(self, attribute, value):
        with pytest.raises(ConfigurationError) as exc:
            build_container(make_settings(**{attribute: value}))

        message = str(exc.value)
        assert value in message, "the message must name the value given"
        assert attribute in message, "the message must name the port"
        assert REGISTRY_FILE in message, "the message must name the registry file"
        assert ADAPTER_TEMPLATE in message, "the message must name the template to copy"
        assert "does not fall back to a mock" in message

    def test_there_is_no_silent_fallback_to_a_mock(self):
        with pytest.raises(ConfigurationError):
            build_container(make_settings(case_file_provider="company"))

    def test_it_lists_the_names_that_are_registered(self):
        with pytest.raises(ConfigurationError) as exc:
            build_container(make_settings(answer_generator="acme"))
        message = str(exc.value)
        assert "fake" in message and "configured" in message

    def test_a_registry_entry_pointing_at_a_missing_module_fails_loudly(self):
        table = {"case_file_provider": {"broken": "uc06.adapters.real.not_written_yet:Adapter"}}
        with pytest.raises(ConfigurationError) as exc:
            ProviderRegistry(table).resolve("case_file_provider", "broken", Settings())
        assert "not_written_yet" in str(exc.value)
        assert ADAPTER_TEMPLATE in str(exc.value)

    def test_a_registry_entry_pointing_at_a_missing_class_fails_loudly(self):
        table = {"case_file_provider": {"broken": "uc06.adapters.mock.case_file:NoSuchAdapter"}}
        with pytest.raises(ConfigurationError) as exc:
            ProviderRegistry(table).resolve("case_file_provider", "broken", Settings())
        assert "NoSuchAdapter" in str(exc.value)

    def test_a_malformed_entry_fails_loudly(self):
        table = {"case_file_provider": {"broken": "uc06.adapters.mock.case_file.MockCaseFileProvider"}}
        with pytest.raises(ConfigurationError) as exc:
            ProviderRegistry(table).resolve("case_file_provider", "broken", Settings())
        assert "module.path:Attribute" in str(exc.value)

    def test_an_unknown_port_key_fails_loudly(self):
        with pytest.raises(ConfigurationError) as exc:
            REGISTRY.resolve("no_such_port", "mock", Settings())
        assert "no_such_port" in str(exc.value)


class TestEagerResolution:
    def test_every_port_is_resolved_at_startup_not_on_first_use(self):
        """A misconfigured provider fails before the first request, not on the
        request that happens to need it."""
        with pytest.raises(ConfigurationError):
            build_container(make_settings(security_incident_sink="siem"))

    def test_the_app_factory_refuses_to_start_on_a_bad_provider(self):
        from uc06.api.app import create_app

        with pytest.raises(ConfigurationError):
            create_app(settings=make_settings(case_file_provider="company"))

    def test_a_valid_configuration_wires_every_port(self, container):
        for attribute in (
            "case_files",
            "learner_context",
            "generator",
            "guard",
            "interactions",
            "halts",
            "admin_alerts",
            "security_incidents",
            "current_user",
        ):
            assert getattr(container, attribute) is not None
