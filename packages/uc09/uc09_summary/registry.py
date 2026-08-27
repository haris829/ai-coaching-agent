"""The provider registry: one lookup, one line per implementation.

This file is the whole of provider selection. There is no ``if setting == ...``
anywhere in the codebase; :func:`build_provider` resolves a port name and a
configured implementation name to a dotted path and imports it.

Adding a real adapter
---------------------

One line in :data:`REGISTRY`::

    "session_provider": {
        "mock": "uc09_summary.adapters.mock.session:MockSessionProvider",
        "foreign": "uc09_summary.adapters.foreign.session:ForeignSessionProvider",
        "company": "uc09_summary.adapters.real.company_session:CompanySessionProvider",
    }

Then set ``UC09_SESSION_PROVIDER=company``. No other file in the repository
learns that the adapter exists - not the API layer, not the services, not the
composition root, not an existing test. The conformance suite parameterises
over the keys of this table, so the new adapter is enrolled in contract testing
by that same one line.

Implementations are named by dotted path and imported lazily, so a real adapter
that needs a library nobody has installed locally cannot break startup for
someone running the mock configuration.

Construction contract
---------------------

Every registered implementation exposes ``from_settings(settings) -> instance``.
That is the only thing the registry requires of it.
"""

from __future__ import annotations

import importlib
from typing import Any

from uc09_summary.config import ENV_PREFIX, Settings
from uc09_summary.domain.errors import ProviderNotRegistered

#: port name -> implementation name -> "module.path:AttributeName".
#: Adding an implementation is exactly one line in this table.
REGISTRY: dict[str, dict[str, str]] = {
    "session_provider": {
        "mock": "uc09_summary.adapters.mock.session:MockSessionProvider",
        "foreign": "uc09_summary.adapters.foreign.session:ForeignSessionProvider",
        "larrycore": "uc09_summary.adapters.real.larrycore_session:LarryCoreSessionProvider",
    },
    "interaction_provider": {
        "mock": "uc09_summary.adapters.mock.interaction:MockInteractionProvider",
        "foreign": "uc09_summary.adapters.foreign.interaction:ForeignInteractionProvider",
    },
    "citation_provider": {
        "mock": "uc09_summary.adapters.mock.citation:MockCitationProvider",
        "foreign": "uc09_summary.adapters.foreign.citation:ForeignCitationProvider",
    },
    "gap_report_provider": {
        "mock": "uc09_summary.adapters.mock.gap_report:MockGapReportProvider",
        "foreign": "uc09_summary.adapters.foreign.gap_report:ForeignGapReportProvider",
    },
    "summary_generator": {
        "fake": "uc09_summary.adapters.mock.generator:DeterministicSummaryGenerator",
        # Wired, and disabled by default: the shipped default is "fake".
        # Refuses to start unless its endpoint is configured.
        "http": "uc09_summary.adapters.real.http_generator:ConfiguredHttpSummaryGenerator",
    },
    "document_renderer": {
        "simple": "uc09_summary.adapters.real.pdf_renderer:SimplePdfRenderer",
        "fake": "uc09_summary.adapters.mock.renderer:FakeDocumentRenderer",
    },
    "summary_repository": {
        "memory": "uc09_summary.adapters.memory.summary_repository:InMemorySummaryRepository",
    },
    "download_log_repository": {
        "memory": "uc09_summary.adapters.memory.download_log:InMemoryDownloadLogRepository",
    },
    "clock": {
        "system": "uc09_summary.adapters.real.clock:SystemClock",
        "fixed": "uc09_summary.adapters.mock.clock:FixedClock",
    },
    "current_user_provider": {
        "header": "uc09_summary.adapters.real.identity:HeaderIdentityProvider",
    },
}

#: Where an engineer should put a new adapter for each port. Used in the
#: startup failure message so that the fix is stated, not guessed.
EXPECTED_ADAPTER_LOCATION: dict[str, str] = {
    "session_provider": "uc09_summary/adapters/real/<vendor>_session.py",
    "interaction_provider": "uc09_summary/adapters/real/<vendor>_interaction.py",
    "citation_provider": "uc09_summary/adapters/real/<vendor>_citation.py",
    "gap_report_provider": "uc09_summary/adapters/real/<vendor>_gap_report.py",
    "summary_generator": "uc09_summary/adapters/real/<vendor>_generator.py",
    "document_renderer": "uc09_summary/adapters/real/<vendor>_renderer.py",
    "summary_repository": "uc09_summary/adapters/real/<vendor>_summary_repository.py",
    "download_log_repository": "uc09_summary/adapters/real/<vendor>_download_log.py",
    "clock": "uc09_summary/adapters/real/clock.py",
    "current_user_provider": "uc09_summary/adapters/real/identity.py",
}


def env_var_for(port: str) -> str:
    """Return the environment variable that selects the implementation for ``port``."""
    return f"{ENV_PREFIX}{port.upper()}"


def registered_names(port: str) -> tuple[str, ...]:
    """Return the implementation names registered for ``port``, sorted."""
    _require_known_port(port)
    return tuple(sorted(REGISTRY[port]))


def resolve_target(port: str, name: str) -> str:
    """Return the dotted path registered for ``(port, name)``.

    Raises:
        ProviderNotRegistered: no such port, or no such implementation. The
            message names the missing key, the variable that selected it, the
            names that do exist, and the file expected to supply the missing
            one.
    """
    _require_known_port(port)
    try:
        return REGISTRY[port][name]
    except KeyError:
        raise ProviderNotRegistered(
            f"No implementation named {name!r} is registered for port {port!r}. "
            f"Selected by {env_var_for(port)}={name!r}. "
            f"Registered names: {', '.join(registered_names(port)) or '(none)'}. "
            f"To add it: create {EXPECTED_ADAPTER_LOCATION.get(port, 'an adapter module')}, "
            f"then add one line to REGISTRY[{port!r}] in uc09_summary/registry.py "
            f"mapping {name!r} to that class. "
            "Startup is refused rather than falling back to a mock."
        ) from None


def load_implementation(port: str, name: str) -> Any:
    """Import and return the class registered for ``(port, name)``."""
    target = resolve_target(port, name)
    module_path, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ProviderNotRegistered(
            f"Port {port!r} implementation {name!r} is registered as {target!r} "
            f"but the module could not be imported: {exc}. "
            "Fix the dotted path in uc09_summary/registry.py or install the "
            "dependency the adapter needs."
        ) from exc
    try:
        return getattr(module, attribute)
    except AttributeError:
        raise ProviderNotRegistered(
            f"Port {port!r} implementation {name!r} is registered as {target!r} "
            f"but {module_path!r} has no attribute {attribute!r}."
        ) from None


def build_provider(port: str, settings: Settings) -> Any:
    """Construct the implementation configured for ``port``.

    Args:
        port: logical port name, matching the settings field.
        settings: application settings; also supplies the implementation name.

    Returns:
        A ready instance.

    Raises:
        ProviderNotRegistered: the configured name has no implementation, or
            the implementation does not expose ``from_settings``.
    """
    name = getattr(settings, port)
    implementation = load_implementation(port, name)
    factory = getattr(implementation, "from_settings", None)
    if factory is None:
        raise ProviderNotRegistered(
            f"{implementation.__module__}.{implementation.__qualname__} is registered "
            f"for port {port!r} but does not expose the required "
            "classmethod from_settings(settings). Copy "
            "uc09_summary/adapters/real/_template.py, which has it."
        )
    return factory(settings)


def _require_known_port(port: str) -> None:
    if port not in REGISTRY:
        raise ProviderNotRegistered(
            f"Unknown port {port!r}. Known ports: {', '.join(sorted(REGISTRY))}."
        )
