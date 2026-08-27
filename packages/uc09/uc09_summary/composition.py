"""Composition root.

Builds every provider from the registry once at startup and hands them to the
service. This is the only module that knows which implementations exist, and it
learns that from :data:`uc09_summary.registry.REGISTRY` rather than from any
conditional of its own.

Startup is eager and strict: every configured provider is constructed here, so
a name with no implementation fails immediately and by name, before the process
accepts a request. There is no path in this file that substitutes a mock for a
provider that was asked for and could not be built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from uc09_summary.application.summary_service import SummaryService
from uc09_summary.config import Settings, load_settings
from uc09_summary.logging_setup import configure_logging, get_logger
from uc09_summary.ports import CurrentUserProvider
from uc09_summary.registry import build_provider

_log = get_logger(__name__)

#: Ports built at startup, in construction order.
PORTS = (
    "session_provider",
    "interaction_provider",
    "citation_provider",
    "gap_report_provider",
    "summary_generator",
    "document_renderer",
    "summary_repository",
    "download_log_repository",
    "clock",
    "current_user_provider",
)


@dataclass
class Container:
    """Everything the API layer needs, already wired."""

    settings: Settings
    providers: dict[str, Any]
    service: SummaryService

    @property
    def current_user_provider(self) -> CurrentUserProvider:
        return self.providers["current_user_provider"]


def build_container(
    settings: Settings | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> Container:
    """Construct the application container.

    Args:
        settings: application settings. Loaded from the environment if omitted.
        overrides: already-built provider instances, keyed by port name. Used by
            tests to install a specific scenario adapter. Overriding a port here
            bypasses the registry for that port only; every other port is still
            built from configuration.

    Returns:
        A wired :class:`Container`.

    Raises:
        ProviderNotRegistered: a configured provider name has no implementation.
            Raised before any request is served, naming the missing key and the
            file expected to supply it.
    """
    settings = settings or load_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    supplied = dict(overrides or {})
    providers: dict[str, Any] = {}
    for port in PORTS:
        if port in supplied:
            providers[port] = supplied[port]
            continue
        providers[port] = build_provider(port, settings)

    _log.info(
        "providers_configured",
        **{port: getattr(settings, port) for port in PORTS},
    )

    service = SummaryService(
        sessions=providers["session_provider"],
        interactions=providers["interaction_provider"],
        citations=providers["citation_provider"],
        gap_reports=providers["gap_report_provider"],
        generator=providers["summary_generator"],
        renderer=providers["document_renderer"],
        summaries=providers["summary_repository"],
        downloads=providers["download_log_repository"],
        clock=providers["clock"],
    )
    return Container(settings=settings, providers=providers, service=service)
