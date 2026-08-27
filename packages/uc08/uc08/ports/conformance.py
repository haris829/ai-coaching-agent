"""The conformance harness protocol every upstream adapter must satisfy (A-24).

The contract conformance suite in ``tests/conformance/`` is adapter-agnostic: it
discovers adapters from the provider registry and drives each one through the
named states below. That is what makes "no new test needs writing to validate a
real adapter" true -- the company engineer implements
``conformance_scenarios`` on the class they copied from
``uc08/adapters/real/_template.py`` and the existing suite covers them.

A scenario builder takes a :class:`~uc08.ports.clock.Clock` (so the fixture data
can be positioned relative to a fake now) and returns a configured adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from uc08.ports.clock import Clock

#: The states every upstream adapter must be able to reproduce.
#:
#: available   -- the upstream answers with usable data
#: empty       -- the upstream answers, and the answer is "nothing"
#: unavailable -- the upstream cannot be reached (ProviderUnavailable)
#: timeout     -- the upstream exceeds the deadline (ProviderTimeout)
#: invalid     -- the upstream answers with something unmappable
#:                (ProviderInvalidResponse, or a normalised default with
#:                 status=invalid where the contract says to degrade)
REQUIRED_CONFORMANCE_SCENARIOS: tuple[str, ...] = (
    "available",
    "empty",
    "unavailable",
    "timeout",
    "invalid",
)

#: The account id the conformance suite asks about. Every scenario must have
#: data (or a fault) for this id.
CONFORMANCE_USER_ID = "conformance-user"

#: Question counts the badge boundary is tested at. An adapter that declares
#: these scenarios joins the behavioural equivalence suite automatically.
BEHAVIOURAL_QUESTION_COUNTS: tuple[int, ...] = (9, 10, 11, 49, 50, 99, 100, 150)

#: Scope-named activity states. An adapter family declaring all of these is
#: pulled into ``tests/integration/test_foreign_adapter_swap.py``, which runs
#: the unmodified service against every declaring family and requires identical
#: results. Only the five ``REQUIRED_CONFORMANCE_SCENARIOS`` are mandatory for a
#: new adapter; these are how a family opts in to the stronger proof.
BEHAVIOURAL_ACTIVITY_SCENARIOS: tuple[str, ...] = (
    "activity_23h59m_ago",
    "activity_24h01m_ago",
    "multiple_interactions_same_day",
    "no_activity",
) + tuple(f"question_count_{count}" for count in BEHAVIOURAL_QUESTION_COUNTS)

#: Scope-named gap report states.
BEHAVIOURAL_GAP_REPORT_SCENARIOS: tuple[str, ...] = (
    "suggestion_available",
    "no_suggestion",
    "unavailable",
)


@runtime_checkable
class ConformanceCapable(Protocol):
    """A provider class that can be exercised by the conformance suite."""

    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], Any]]:
        ...
