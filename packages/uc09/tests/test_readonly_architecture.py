"""Architecture test: upstream ports and adapters expose no mutating method.

Read-only is enforced structurally rather than by convention. The test walks
:data:`REGISTRY`, so a new adapter is covered by its one registry line and
cannot quietly introduce a write path.
"""

from __future__ import annotations

import inspect

import pytest

from tests.support.readonly import looks_like_mutation, mutating_methods, public_methods
from uc09_summary.ports import (
    UPSTREAM_READ_ONLY_PORTS,
    CitationProvider,
    GapReportProvider,
    InteractionProvider,
    SessionProvider,
)
from uc09_summary.registry import REGISTRY, load_implementation

#: The single read method each upstream port declares.
PORT_READ_METHODS = {
    "session_provider": ("get_session",),
    "interaction_provider": ("for_session",),
    "citation_provider": ("for_session",),
    "gap_report_provider": ("suggestions",),
}

PORT_PROTOCOLS = {
    "session_provider": SessionProvider,
    "interaction_provider": InteractionProvider,
    "citation_provider": CitationProvider,
    "gap_report_provider": GapReportProvider,
}


def _registered_upstream_adapters():
    for port in UPSTREAM_READ_ONLY_PORTS:
        for name in sorted(REGISTRY[port]):
            yield port, name


UPSTREAM_ADAPTERS = list(_registered_upstream_adapters())


class TestUpstreamPortsAreReadOnlyByShape:
    @pytest.mark.parametrize("port", UPSTREAM_READ_ONLY_PORTS)
    def test_the_protocol_declares_only_its_read_method(self, port: str) -> None:
        protocol = PORT_PROTOCOLS[port]
        assert public_methods(protocol) == list(PORT_READ_METHODS[port]), (
            f"The {port} protocol must declare exactly "
            f"{PORT_READ_METHODS[port]} and nothing else. Adding a write "
            "method to an upstream port is how a read-only guarantee is lost."
        )

    @pytest.mark.parametrize("port", UPSTREAM_READ_ONLY_PORTS)
    def test_the_protocol_has_no_mutating_method(self, port: str) -> None:
        assert mutating_methods(PORT_PROTOCOLS[port], PORT_READ_METHODS[port]) == []


class TestRegisteredUpstreamAdaptersAreReadOnly:
    @pytest.mark.parametrize(
        ("port", "name"), UPSTREAM_ADAPTERS, ids=[f"{p}:{n}" for p, n in UPSTREAM_ADAPTERS]
    )
    def test_the_adapter_exposes_no_mutating_method(self, port: str, name: str) -> None:
        implementation = load_implementation(port, name)
        offenders = mutating_methods(implementation, PORT_READ_METHODS[port])

        assert offenders == [], (
            f"{implementation.__qualname__} exposes {offenders}. Upstream "
            "adapters read; they never write. If a real integration needs a "
            "write, that is a contract conversation, not an extra method here."
        )

    @pytest.mark.parametrize(
        ("port", "name"), UPSTREAM_ADAPTERS, ids=[f"{p}:{n}" for p, n in UPSTREAM_ADAPTERS]
    )
    def test_the_public_surface_is_exactly_the_port(self, port: str, name: str) -> None:
        implementation = load_implementation(port, name)
        allowed = set(PORT_READ_METHODS[port]) | {"from_settings", "conformance_profile"}
        extra = set(public_methods(implementation)) - allowed

        assert not extra, (
            f"{implementation.__qualname__} exposes public method(s) {sorted(extra)} "
            "beyond the port. An upstream adapter offers the port surface and "
            "nothing else, so no caller can reach past the contract."
        )

    @pytest.mark.parametrize(
        ("port", "name"), UPSTREAM_ADAPTERS, ids=[f"{p}:{n}" for p, n in UPSTREAM_ADAPTERS]
    )
    def test_no_private_helper_mutates_either(self, port: str, name: str) -> None:
        implementation = load_implementation(port, name)
        for method_name, _member in inspect.getmembers(implementation, inspect.isfunction):
            if method_name.startswith("__"):
                continue
            assert not looks_like_mutation(method_name), (
                f"{implementation.__qualname__}.{method_name} looks like a "
                "write. Read-only applies to the whole adapter, not only to "
                "its public surface."
            )


class TestTheServiceNeverWritesUpstream:
    def test_the_service_holds_no_upstream_writer(self) -> None:
        from tests.support.harness import build_harness

        harness = build_harness()
        service = harness.service

        for attribute in ("_sessions", "_interactions", "_citations", "_gap_reports"):
            adapter = getattr(service, attribute)
            offenders = mutating_methods(type(adapter), tuple(public_methods(type(adapter))))
            assert offenders == []

    def test_only_the_repositories_declare_writes(self) -> None:
        """Writing is confined to the two ports this component owns."""
        from uc09_summary.ports import DownloadLogRepository, SummaryRepository

        assert "save" in public_methods(SummaryRepository)
        assert "record" in public_methods(DownloadLogRepository)

        for port in UPSTREAM_READ_ONLY_PORTS:
            assert "save" not in public_methods(PORT_PROTOCOLS[port])
            assert "record" not in public_methods(PORT_PROTOCOLS[port])

    def test_the_upstream_port_list_covers_every_upstream_port(self) -> None:
        """A new upstream port must be added to the read-only list."""
        upstream = {
            port
            for port in REGISTRY
            if port
            not in {
                "summary_generator",
                "document_renderer",
                "summary_repository",
                "download_log_repository",
                "clock",
                "current_user_provider",
            }
        }
        assert upstream == set(UPSTREAM_READ_ONLY_PORTS), (
            "Every upstream port must appear in UPSTREAM_READ_ONLY_PORTS, or "
            "it escapes this architecture test."
        )
