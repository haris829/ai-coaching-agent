"""Provider registry - selection is a single lookup, never a chain of ifs.

A provider is a dotted `module:attribute` path. Registering one is a single line
in the registry table in uc06/composition.py, and no other file in the repository
learns that the new adapter exists: nothing imports it, nothing branches on it,
no test enumerates it by name.

Two rules this module exists to enforce:

* An unknown provider name fails LOUDLY at startup, naming the port, the value
  given, the registry file and the template to copy.
* There is never a silent fallback to a mock. A service quietly running on fake
  data in production is worse than one that refuses to start.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Mapping

from .config import Settings
from .domain.errors import ConfigurationError

#: A registry entry is either a dotted path "package.module:Attribute" or a
#: callable taking Settings.
Entry = str | Callable[[Settings], Any]

REGISTRY_FILE = "uc06/composition.py"
ADAPTER_TEMPLATE = "uc06/adapters/real/_template.py"


class ProviderRegistry:
    def __init__(self, table: Mapping[str, Mapping[str, Entry]]) -> None:
        self._table = {port: dict(entries) for port, entries in table.items()}

    def ports(self) -> tuple[str, ...]:
        return tuple(self._table)

    def names_for(self, port_key: str) -> tuple[str, ...]:
        return tuple(self._table.get(port_key, {}))

    def resolve(self, port_key: str, provider_name: str, settings: Settings) -> Any:
        entries = self._table.get(port_key)
        if entries is None:
            raise ConfigurationError(
                f"Unknown port key {port_key!r}. Known ports: {', '.join(sorted(self._table))}. "
                f"Ports are declared in {REGISTRY_FILE}."
            )
        entry = entries.get(provider_name)
        if entry is None:
            known = ", ".join(sorted(entries)) or "(none)"
            raise ConfigurationError(
                f"No implementation registered for port {port_key!r} under provider name "
                f"{provider_name!r}. Registered names for this port: {known}. "
                f"To add one: create the adapter by copying {ADAPTER_TEMPLATE}, then add exactly one "
                f"line to the {port_key!r} entry of PROVIDER_REGISTRY in {REGISTRY_FILE}: "
                f'"{provider_name}": "uc06.adapters.real.{provider_name}_{port_key}:YourAdapter". '
                "UC-06 does not fall back to a mock when a provider is misconfigured."
            )
        factory = self._load(entry, port_key, provider_name)
        return factory(settings)

    @staticmethod
    def _load(entry: Entry, port_key: str, provider_name: str) -> Callable[[Settings], Any]:
        if callable(entry):
            return entry
        if ":" not in entry:
            raise ConfigurationError(
                f"Registry entry for {port_key}/{provider_name} must be "
                f'"module.path:Attribute", got {entry!r} (see {REGISTRY_FILE}).'
            )
        module_path, _, attribute = entry.partition(":")
        try:
            module = import_module(module_path)
        except ImportError as exc:
            raise ConfigurationError(
                f"Registry entry for {port_key}/{provider_name} points at module {module_path!r}, "
                f"which could not be imported. Create it from {ADAPTER_TEMPLATE}. Import error: {exc}"
            ) from exc
        try:
            return getattr(module, attribute)
        except AttributeError as exc:
            raise ConfigurationError(
                f"Registry entry for {port_key}/{provider_name} points at {attribute!r} in "
                f"{module_path!r}, which does not define it."
            ) from exc
