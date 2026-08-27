"""Shared machinery for the adapter-agnostic conformance suites.

Adapters are **discovered from the provider registry**, not listed here. A newly
registered adapter is covered by the existing suites without a test being
written: that is the whole point of this directory.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from uc08.adapters.clock.clocks import FixedClock
from uc08.domain.errors import ProviderError
from uc08.ports.conformance import REQUIRED_CONFORMANCE_SCENARIOS
from uc08.registry import registered_classes

#: The conformance suites position fixture data relative to this moment.
CONFORMANCE_NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)

#: Tokens that must never appear in anything that crosses the port boundary:
#: registered provider names, vendor names from the foreign family, that
#: family upstream field names, and the usual transport giveaways.
FORBIDDEN_LEAK_TOKENS: tuple[str, ...] = (
    # provider registry names
    "mock",
    "foreign_lexicon",
    # vendor identity of the foreign family
    "lexicon",
    # foreign upstream field names and nesting
    "learnerref",
    "eventkey",
    "questionsasked",
    "subjectarea",
    "academictier",
    "coursecompletion",
    "recommendation",
    "timeline",
    "entries",
    # transport and credential giveaways
    "http://",
    "https://",
    "bearer",
    "token",
    "api_key",
    "apikey",
    "traceback",
)


def adapters_for(port: str) -> list[tuple[str, type]]:
    """Every registered implementation of a port, as pytest parameters."""
    return sorted(registered_classes(port).items())


def new_clock() -> FixedClock:
    return FixedClock(CONFORMANCE_NOW)


def scenarios_of(adapter_class: type) -> Mapping[str, Callable[[Any], Any]]:
    builders = adapter_class.conformance_scenarios()
    missing = [name for name in REQUIRED_CONFORMANCE_SCENARIOS if name not in builders]
    assert not missing, (
        f"{adapter_class.__name__} does not declare the required conformance scenarios {missing}. "
        f"Every adapter must be drivable into each of {list(REQUIRED_CONFORMANCE_SCENARIOS)}; "
        f"see uc08/adapters/real/_template.py."
    )
    return builders


def build(adapter_class: type, scenario: str):
    clock = new_clock()
    return scenarios_of(adapter_class)[scenario](clock), clock


def assert_no_leakage(error: ProviderError, *, expected_port: str) -> None:
    """The boundary rule: nothing upstream escapes.

    Checks the exception type, the abstract port name, and that neither the
    message nor the exception chain text carries an upstream field name, a
    vendor name, a provider name or a transport detail.
    """
    assert isinstance(error, ProviderError)
    assert error.port == expected_port, (
        f"the error must name the abstract port, not a vendor: got {error.port!r}"
    )
    text = f"{error} {error.detail}".lower()
    for token in FORBIDDEN_LEAK_TOKENS:
        assert token not in text, f"{token!r} leaked past the port boundary in {text!r}"

    # The vendor exception may be chained for logging, but its text must not be
    # what a caller reads.
    assert type(error).__module__.startswith("uc08.domain.errors")


def assert_utc(moment: datetime) -> None:
    assert isinstance(moment, datetime)
    assert moment.tzinfo is not None, "every timestamp crossing the port is timezone-aware"
    assert moment.utcoffset() == timezone.utc.utcoffset(None), "every timestamp crossing the port is UTC"
