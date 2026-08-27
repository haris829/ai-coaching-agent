"""Provider registries.

Provider selection is a **single lookup keyed on config**, never a chain of
conditionals.  Adding a provider is one line: an ``@REGISTRY.register("key")``
decorator on the new adapter class, plus that adapter's module appearing in
``uc05.composition.ADAPTER_MODULES``.  No other file learns that the adapter
exists.

Two properties matter more than the mechanism:

*   **Fail loudly.**  A configured key with no registered implementation
    raises ``UnknownProvider`` at startup, naming the missing key, the
    environment variable that selected it and the file expected to supply it.
*   **Never fall back.**  There is no silent substitution of a mock for a real
    provider.  A service quietly running on fake data in production is worse
    than one that refuses to start.
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

from .domain.errors import UnknownProvider

T = TypeVar("T")

#: Every registry created, so that ``describe_registries()`` can render the
#: full picture for diagnostics and for ``docs/INTEGRATION.md``.
_ALL_REGISTRIES: list["ProviderRegistry"] = []


class ProviderRegistry(Generic[T]):
    """A named registry of factories for one port."""

    def __init__(self, port_name: str, env_var: str, symbol: str) -> None:
        self.port_name = port_name
        self.env_var = env_var
        #: The module-level name of this registry, so the "provider not
        #: found" message can name the exact decorator to write.
        self.symbol = symbol
        self._factories: dict[str, Callable[..., T]] = {}
        self._origins: dict[str, str] = {}
        _ALL_REGISTRIES.append(self)

    # -- registration ----------------------------------------------------

    def register(self, key: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator form.  This is the "one line" of the integration rule."""

        def decorate(factory: Callable[..., T]) -> Callable[..., T]:
            if key in self._factories:
                raise UnknownProvider(
                    f"duplicate provider key {key!r} for port {self.port_name!r}: "
                    f"already registered by {self._origins[key]}"
                )
            self._factories[key] = factory
            self._origins[key] = getattr(factory, "__module__", "<unknown>")
            return factory

        return decorate

    # -- resolution ------------------------------------------------------

    def create(self, key: str, /, **kwargs: object) -> T:
        if key not in self._factories:
            raise UnknownProvider(self._missing_message(key))
        return self._factories[key](**kwargs)

    def _missing_message(self, key: str) -> str:
        known = ", ".join(sorted(self._factories)) or "<none registered>"
        return (
            f"No implementation registered for {self.port_name} provider {key!r} "
            f"(selected by {self.env_var}={key}).\n"
            f"  Registered keys: {known}\n"
            f"  Expected: a class decorated with "
            f"@{self._registry_symbol()}.register({key!r}) in "
            f"uc05/adapters/real/{key}_{self.port_name}.py, and that module "
            f"listed in uc05.composition.ADAPTER_MODULES.\n"
            f"  Import the registry as: "
            f"from uc05.registry import {self._registry_symbol()}\n"
            f"  Start from the skeleton in uc05/adapters/real/_template.py.\n"
            f"  Refusing to start rather than falling back to a mock."
        )

    def _registry_symbol(self) -> str:
        return self.symbol

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def origin_of(self, key: str) -> str | None:
        return self._origins.get(key)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<ProviderRegistry {self.port_name} keys={self.keys()}>"


# --------------------------------------------------------------------------
# One registry per port.  The env var named here is the *only* thing an
# integration engineer changes to switch implementation.
# --------------------------------------------------------------------------

GUIDING_QUESTION_REGISTRY: ProviderRegistry = ProviderRegistry(
    "guiding_question_generator", "GENERATOR", "GUIDING_QUESTION_REGISTRY"
)
ANSWER_REGISTRY: ProviderRegistry = ProviderRegistry(
    "answer_generator", "GENERATOR", "ANSWER_REGISTRY"
)
LEARNER_CONTEXT_REGISTRY: ProviderRegistry = ProviderRegistry(
    "learner_context_provider", "LEARNER_CONTEXT_PROVIDER", "LEARNER_CONTEXT_REGISTRY"
)
INTENT_REGISTRY: ProviderRegistry = ProviderRegistry(
    "intent_classifier", "INTENT_CLASSIFIER", "INTENT_REGISTRY"
)
DIALOGUE_REPOSITORY_REGISTRY: ProviderRegistry = ProviderRegistry(
    "dialogue_repository", "DIALOGUE_REPOSITORY", "DIALOGUE_REPOSITORY_REGISTRY"
)
SESSION_MODE_REPOSITORY_REGISTRY: ProviderRegistry = ProviderRegistry(
    "session_mode_repository",
    "SESSION_MODE_REPOSITORY",
    "SESSION_MODE_REPOSITORY_REGISTRY",
)
INTERACTION_LOG_REPOSITORY_REGISTRY: ProviderRegistry = ProviderRegistry(
    "interaction_log_repository",
    "INTERACTION_LOG_REPOSITORY",
    "INTERACTION_LOG_REPOSITORY_REGISTRY",
)
CURRENT_USER_REGISTRY: ProviderRegistry = ProviderRegistry(
    "current_user_provider", "CURRENT_USER_PROVIDER", "CURRENT_USER_REGISTRY"
)

REGISTRIES: dict[str, ProviderRegistry] = {
    registry.port_name: registry for registry in _ALL_REGISTRIES
}


def describe_registries() -> dict[str, dict[str, object]]:
    """Diagnostics only.  Never returned to a client."""
    return {
        name: {"env_var": registry.env_var, "keys": list(registry.keys())}
        for name, registry in REGISTRIES.items()
    }
