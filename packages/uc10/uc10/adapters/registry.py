"""The provider registry.

Provider selection is a single dictionary lookup keyed on configuration.  Adding a
provider is ONE line in THIS file and nothing else: no conditional chain, no other module
learns that a new adapter exists.

An unregistered key fails loudly at startup, naming the missing key, the registered keys,
this file, and the adapter file expected to supply it.  There is no fallback to a mock: a
service quietly running on fake data in production is worse than one that refuses to
start.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from uc10.adapters.foreign.interaction_provider import ForeignInteractionProvider
from uc10.adapters.mock.interaction_provider import MockInteractionProvider
from uc10.config import Settings
from uc10.ports.clock import Clock
from uc10.ports.interaction_provider import InteractionProvider


@dataclass(frozen=True, slots=True)
class ProviderContext:
    """Everything a provider factory is allowed to depend on."""

    settings: Settings
    clock: Clock


InteractionProviderFactory = Callable[[ProviderContext], InteractionProvider]


# ---------------------------------------------------------------------------
# InteractionProvider implementations.
# ADD A REAL PROVIDER HERE -- one line, this file only:
#     "company": lambda ctx: CompanyInteractionProvider(clock=ctx.clock),
# ---------------------------------------------------------------------------
INTERACTION_PROVIDERS: dict[str, InteractionProviderFactory] = {
    "mock": lambda ctx: MockInteractionProvider(clock=ctx.clock),
    "foreign_demo": lambda ctx: ForeignInteractionProvider(clock=ctx.clock),
}


class UnknownProviderError(RuntimeError):
    """Raised at startup when configuration names a provider nobody registered."""

    def __init__(self, *, port: str, key: str, registered: list[str], env_var: str) -> None:
        expected_file = f"uc10/adapters/real/{key}_{_snake(port)}.py"
        super().__init__(
            f"{env_var}={key!r} names no registered {port} implementation. "
            f"Registered keys: {', '.join(sorted(registered)) or '(none)'}. "
            f"To add it: create {expected_file} from uc10/adapters/real/_template.py, "
            f"then add one line to INTERACTION_PROVIDERS in uc10/adapters/registry.py "
            f"mapping {key!r} to its factory. "
            f"This service will not fall back to a mock."
        )
        self.port = port
        self.key = key


def _snake(name: str) -> str:
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


def build_interaction_provider(context: ProviderContext) -> InteractionProvider:
    key = context.settings.interaction_provider
    factory = INTERACTION_PROVIDERS.get(key)
    if factory is None:
        raise UnknownProviderError(
            port="InteractionProvider",
            key=key,
            registered=list(INTERACTION_PROVIDERS),
            env_var="INTERACTION_PROVIDER",
        )
    return factory(context)


def register_interaction_provider(key: str, factory: InteractionProviderFactory) -> None:
    """Registration hook, equivalent to adding the dictionary line.

    Exists so a conformance run can point the whole service at a candidate adapter
    without editing this file.
    """
    INTERACTION_PROVIDERS[key] = factory
