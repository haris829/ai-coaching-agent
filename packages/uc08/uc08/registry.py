"""The provider registry: one lookup, one line per provider, one file.

Adding a provider is a single entry in the table for its port. No other file in
the repository learns that a new adapter exists -- not the composition root
wiring, not the services, not the API, not the tests.

There are no conditionals here on purpose. A chain of ``if setting == ...``
would force every new integration to edit shared code; a table does not.

Two rules this module enforces:

1. A configured provider name with no entry raises
   :class:`~uc08.domain.errors.ProviderNotRegistered` **at startup**, naming the
   missing key, the table it belongs in, and the file expected to supply it.
2. There is no fallback. A service that quietly runs on fake data in production
   is worse than one that refuses to start.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, TypeVar

from uc08.domain.errors import ProviderNotRegistered, ProviderRegistrationBroken
from uc08.ports.upstream import ActivityProvider, GapReportProvider

T = TypeVar("T")


@dataclass(frozen=True)
class ProviderEntry:
    """One registered implementation.

    ``target`` is ``"module.path:ClassName"``, imported lazily, so registering a
    provider costs nothing until it is selected and an unused adapter cannot
    break startup.
    """

    target: str
    summary: str


# ==========================================================================
#  ACTIVITY PROVIDERS -- add one line to integrate a real activity read model
# ==========================================================================
ACTIVITY_PROVIDERS: dict[str, ProviderEntry] = {
    "mock": ProviderEntry(
        "uc08.adapters.mock.activity:MockActivityProvider",
        "in-process deterministic activity read model",
    ),
    "foreign_lexicon": ProviderEntry(
        "uc08.adapters.foreign.activity:ForeignActivityAdapter",
        "deliberately foreign payload family, used to prove replaceability",
    ),
    # <-- one line here for a real activity provider, e.g.
    # "company": ProviderEntry("uc08.adapters.real.activity:CompanyActivityAdapter", "..."),
}

# ==========================================================================
#  GAP REPORT PROVIDERS -- add one line to integrate a real gap report
# ==========================================================================
GAP_REPORT_PROVIDERS: dict[str, ProviderEntry] = {
    "mock": ProviderEntry(
        "uc08.adapters.mock.gap_report:MockGapReportProvider",
        "in-process deterministic gap report",
    ),
    "foreign_lexicon": ProviderEntry(
        "uc08.adapters.foreign.gap_report:ForeignGapReportAdapter",
        "deliberately foreign payload family, used to prove replaceability",
    ),
    # <-- one line here for a real gap report provider.
}


#: Port name -> (registry table, config variable, required base class).
REGISTRIES: dict[str, tuple[dict[str, ProviderEntry], str, type]] = {
    "activity": (ACTIVITY_PROVIDERS, "ACTIVITY_PROVIDER", ActivityProvider),
    "gap_report": (GAP_REPORT_PROVIDERS, "GAP_REPORT_PROVIDER", GapReportProvider),
}


def resolve_provider_class(port: str, name: str) -> type:
    """Look up and import the class registered for ``name`` on ``port``.

    Raises loudly, at startup, if the key is absent or the entry is broken.
    """
    if port not in REGISTRIES:
        raise ProviderNotRegistered(
            f"no registry exists for port {port!r}; known ports: {sorted(REGISTRIES)}"
        )
    table, env_var, base = REGISTRIES[port]
    entry = table.get(name)
    if entry is None:
        raise ProviderNotRegistered(
            f"{env_var}={name!r} has no registered implementation. "
            f"Registered names for the {port!r} port: {sorted(table)}. "
            f"Add one line to {_registry_symbol(port)} in uc08/registry.py pointing at the class "
            f"that implements {base.__module__}.{base.__name__} "
            f"(start from uc08/adapters/real/_template.py). "
            f"There is no fallback to a mock: refusing to start."
        )

    module_path, _, class_name = entry.target.partition(":")
    if not module_path or not class_name:
        raise ProviderRegistrationBroken(
            f"registry entry for {env_var}={name!r} is malformed: expected 'module.path:ClassName', "
            f"got {entry.target!r}"
        )
    try:
        module = import_module(module_path)
    except ImportError as exc:
        raise ProviderRegistrationBroken(
            f"{env_var}={name!r} points at module {module_path!r}, which could not be imported. "
            f"Expected file: {module_path.replace('.', '/')}.py"
        ) from exc
    try:
        implementation = getattr(module, class_name)
    except AttributeError as exc:
        raise ProviderRegistrationBroken(
            f"{env_var}={name!r} points at {entry.target!r} but {module_path!r} has no {class_name!r}"
        ) from exc
    if not (isinstance(implementation, type) and issubclass(implementation, base)):
        raise ProviderRegistrationBroken(
            f"{env_var}={name!r} resolves to {entry.target!r}, which does not implement "
            f"{base.__module__}.{base.__name__}"
        )
    return implementation


def registered_names(port: str) -> tuple[str, ...]:
    table, _env_var, _base = REGISTRIES[port]
    return tuple(sorted(table))


def registered_classes(port: str) -> dict[str, type]:
    """Every registered implementation for a port, imported.

    Used by the conformance suite to discover adapters, so a newly registered
    adapter is covered without writing a test.
    """
    return {name: resolve_provider_class(port, name) for name in registered_names(port)}


def _registry_symbol(port: str) -> str:
    return {"activity": "ACTIVITY_PROVIDERS", "gap_report": "GAP_REPORT_PROVIDERS"}.get(
        port, f"the {port} registry"
    )


def build_provider(port: str, name: str, clock: Any, *, timeout_seconds: float) -> Any:
    """Construct a provider.

    Every upstream adapter shares one construction signature --
    ``(clock, *, timeout_seconds)`` (A-23) -- so the composition root never needs to
    know which implementation it is building. Adapter-specific configuration is
    read by the adapter itself, from the environment.
    """
    implementation = resolve_provider_class(port, name)
    return implementation(clock, timeout_seconds=timeout_seconds)
