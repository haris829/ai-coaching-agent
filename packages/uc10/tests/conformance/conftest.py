"""Conformance-run configuration.

    pytest tests/conformance -q                       # every built-in adapter
    pytest tests/conformance -q --adapter=company     # one registered adapter
    pytest tests/conformance -q --adapter=company --conformance-fixtures=fixtures.json
"""

from __future__ import annotations

import pytest

from tests.conformance.cases import UnknownAdapter, resolve_cases


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("uc10 conformance")
    group.addoption(
        "--adapter",
        action="store",
        default=None,
        help="Registry key of the adapter under test (default: every built-in adapter).",
    )
    group.addoption(
        "--conformance-fixtures",
        action="store",
        default=None,
        help="JSON file mapping scenario names to identifiers in the real upstream.",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "case" not in metafunc.fixturenames:
        return
    try:
        cases = resolve_cases(
            metafunc.config.getoption("--adapter"),
            metafunc.config.getoption("--conformance-fixtures"),
        )
    except UnknownAdapter as exc:  # a usage error, not a test failure
        raise pytest.UsageError(str(exc)) from exc
    if "scenario" in metafunc.fixturenames:
        pairs = [(case, scenario) for case in cases for scenario in case.scenario_ids()]
        metafunc.parametrize(
            ("case", "scenario"),
            pairs,
            ids=[f"{case.name}-{scenario}" for case, scenario in pairs],
        )
    else:
        metafunc.parametrize("case", cases, ids=[case.name for case in cases])


@pytest.fixture
def provider(case, clock):
    """The adapter under test, built exactly as the composition root would build it."""
    return case.build(clock)
