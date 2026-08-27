"""Parameterisation for the conformance kit.

Adapters under test are resolved from the registry, so a newly registered adapter
is covered automatically. `--adapter-family=<name>` narrows the run to one
provider name where the port has one.

Each port also needs to know which identifiers exercise which contract case in
the adapter's own world - "the case file that is unreachable" is a different
string for every upstream. That map is declared by the adapter, as a module-level
`CONFORMANCE_SCENARIOS` dict in the adapter's own file, and read from there. So
pointing this kit at a new adapter edits **no test file at all**: the adapter
brings its own scenarios with it.

An adapter that does not declare the map fails with a message telling the
engineer exactly what to add. It is never silently skipped.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from uc06.composition import PROVIDER_REGISTRY, REGISTRY
from uc06.config import Settings

#: Contract cases each port's scenario map must name.
CASE_FILE_KEYS = (
    "readable",
    "partial",
    "empty",
    "access_denied",
    "foreign_origin",
    "unavailable",
    "invalid",
    "timeout",
)
LEARNER_CONTEXT_KEYS = ("available", "unavailable", "timeout", "unmappable_level", "no_practice_area")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--adapter-family",
        action="store",
        default=None,
        help="Registry provider name to run the conformance kit against (e.g. mock, foreign, lexos).",
    )


def _families(port_key: str, requested: str | None) -> list[str]:
    """Providers to test for a port.

    With --adapter-family=X: just X, where the port has a provider of that name.
    A port that does not (a company may implement one port and leave the rest on
    the shipped adapters) falls back to everything registered for it, so the run
    still covers the whole contract rather than reporting empty.
    """
    names = list(PROVIDER_REGISTRY[port_key])
    if requested and requested in names:
        return [requested]
    return names


def _adapter(port_key: str, name: str) -> Any:
    return REGISTRY.resolve(port_key, name, Settings())


def _scenarios(adapter: Any, required: tuple[str, ...], port_key: str) -> dict[str, str]:
    """Read the adapter's own scenario declaration."""
    module = sys.modules[type(adapter).__module__]
    declared = getattr(module, "CONFORMANCE_SCENARIOS", None)
    if not isinstance(declared, dict):
        return {
            "__missing__": (
                f"{module.__name__} implements the {port_key} port but declares no "
                f"CONFORMANCE_SCENARIOS dict. Add one at module level naming the identifier in "
                f"your system for each of: {', '.join(required)}."
            )
        }
    return dict(declared)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    requested = metafunc.config.getoption("--adapter-family")

    if "case_file_adapter" in metafunc.fixturenames:
        families = _families("case_file_provider", requested)
        adapters = [_adapter("case_file_provider", family) for family in families]
        metafunc.parametrize(
            "case_file_adapter,case_scenarios",
            [
                (adapter, _scenarios(adapter, CASE_FILE_KEYS, "case_file_provider"))
                for adapter in adapters
            ],
            ids=families,
        )

    if "learner_context_adapter" in metafunc.fixturenames:
        families = _families("learner_context_provider", requested)
        adapters = [_adapter("learner_context_provider", family) for family in families]
        metafunc.parametrize(
            "learner_context_adapter,context_scenarios",
            [
                (adapter, _scenarios(adapter, LEARNER_CONTEXT_KEYS, "learner_context_provider"))
                for adapter in adapters
            ],
            ids=families,
        )

    if "generator_adapter" in metafunc.fixturenames:
        # The configured generator refuses to construct until confidentiality
        # sign-off is recorded, so it cannot be exercised here by design.
        families = [f for f in _families("answer_generator", requested) if f != "configured"]
        metafunc.parametrize(
            "generator_adapter",
            [_adapter("answer_generator", family) for family in families],
            ids=families,
        )

    if "guard_adapter" in metafunc.fixturenames:
        families = _families("guard_classifier", requested)
        metafunc.parametrize(
            "guard_adapter",
            [_adapter("guard_classifier", family) for family in families],
            ids=families,
        )


def scenario(scenarios: dict[str, str], key: str) -> str:
    """Look up a contract case in an adapter's declared scenario map.

    A map that is missing entirely, or missing this case, is a FAILURE - not a
    skip. The contract case exists; an adapter that cannot express it has not
    demonstrated the behaviour, and the run says so out loud.
    """
    if "__missing__" in scenarios:
        pytest.fail(scenarios["__missing__"])
    value = scenarios.get(key)
    if not value:
        pytest.fail(
            f"the adapter's CONFORMANCE_SCENARIOS names no identifier for the '{key}' contract "
            f"case. Every case must be exercisable: if the upstream genuinely cannot produce it, "
            f"that is a contract conversation, not a gap to leave untested."
        )
    return value
