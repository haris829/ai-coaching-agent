"""Adapter cases for the conformance kit.

A case says *which adapter* to test and *which identifiers* exercise each documented
scenario.  The contract suite itself knows nothing about any particular adapter.

Running it against a real adapter needs no new test code:

    pytest tests/conformance -q --adapter=company
    pytest tests/conformance -q --adapter=company --conformance-fixtures=fixtures.json

where ``fixtures.json`` maps scenario names to identifiers in the real system:

    {
      "ok": "abc-123",
      "recent": "abc-124",
      "stale": "abc-125",
      "unavailable": "abc-126",
      "timeout": "abc-127",
      "invalid": "abc-128",
      "unmapped_level": "abc-129",
      "forbidden_tokens": ["theirFieldName", "vendorname"]
    }

Without a fixtures file the shape, error-vocabulary and leakage contracts still run
against the real adapter; the scenario-driven ones need real identifiers to exercise.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from uc10.adapters.registry import INTERACTION_PROVIDERS, ProviderContext
from uc10.config import Settings
from uc10.ports.clock import Clock
from uc10.ports.interaction_provider import InteractionProvider

#: Scenario names the contract suite understands.
SCENARIOS = ("ok", "recent", "stale", "unavailable", "timeout", "invalid", "unmapped_level")


class UnknownAdapter(LookupError):
    """--adapter named a provider nobody registered."""


@dataclass(frozen=True)
class InteractionProviderCase:
    name: str
    build: Callable[[Clock], InteractionProvider]
    ids: dict[str, str] = field(default_factory=dict)
    #: Upstream words that must never appear in anything crossing the port boundary.
    forbidden_tokens: tuple[str, ...] = ()
    unknown_id: str = "conformance-identifier-that-does-not-exist"

    def scenario_ids(self) -> list[str]:
        return [scenario for scenario in SCENARIOS if scenario in self.ids]


def _mock_case() -> InteractionProviderCase:
    from uc10.adapters.mock.interaction_provider import MockInteractionProvider

    return InteractionProviderCase(
        name="mock",
        build=lambda clock: MockInteractionProvider(clock),
        ids={
            "ok": "int_answer",
            "recent": "int_delivered_23h",
            "stale": "int_delivered_25h",
            "unavailable": "int_unavailable",
            "timeout": "int_timeout",
            "invalid": "int_invalid",
            "unmapped_level": "int_naric_invalid",
        },
        forbidden_tokens=("MOCK_QUESTION_TEXT", "MOCK_RESPONSE_TEXT", "MockInteractionProvider"),
    )


def _foreign_case() -> InteractionProviderCase:
    from uc10.adapters.foreign.interaction_provider import ForeignInteractionProvider

    return InteractionProviderCase(
        name="foreign_demo",
        build=lambda clock: ForeignInteractionProvider(clock),
        ids={
            "ok": "TXN-ANSWER",
            "recent": "TXN-23H",
            "stale": "TXN-25H",
            "unavailable": "TXN-DOWN",
            "timeout": "TXN-SLOW",
            "invalid": "TXN-GARBLED",
            "unmapped_level": "TXN-BADLEVEL",
        },
        forbidden_tokens=(
            "txnRef",
            "learnerRef",
            "threadRef",
            "completionPct",
            "EQF",
            "FOREIGN_QUESTION_TEXT",
            "ForeignInteractionProvider",
        ),
    )


BUILT_IN_CASES: dict[str, Callable[[], InteractionProviderCase]] = {
    "mock": _mock_case,
    "foreign_demo": _foreign_case,
}


def case_from_registry(key: str, fixtures_path: str | None) -> InteractionProviderCase:
    """Build a case for any registered adapter, including one delivered by the company."""
    if key in BUILT_IN_CASES and fixtures_path is None:
        return BUILT_IN_CASES[key]()
    if key not in INTERACTION_PROVIDERS:
        raise UnknownAdapter(
            f"--adapter={key!r} is not registered. Add one line to INTERACTION_PROVIDERS "
            f"in uc10/adapters/registry.py first. Registered: {sorted(INTERACTION_PROVIDERS)}"
        )
    fixtures: dict = {}
    if fixtures_path:
        fixtures = json.loads(Path(fixtures_path).read_text(encoding="utf-8"))

    def build(clock: Clock) -> InteractionProvider:
        settings = Settings(_env_file=None).model_copy(update={"interaction_provider": key})
        return INTERACTION_PROVIDERS[key](ProviderContext(settings=settings, clock=clock))

    return InteractionProviderCase(
        name=key,
        build=build,
        ids={name: fixtures[name] for name in SCENARIOS if name in fixtures},
        forbidden_tokens=tuple(fixtures.get("forbidden_tokens", ())),
        unknown_id=fixtures.get(
            "unknown_id", "conformance-identifier-that-does-not-exist"
        ),
    )


def resolve_cases(adapter: str | None, fixtures_path: str | None) -> list[InteractionProviderCase]:
    if adapter:
        return [case_from_registry(adapter, fixtures_path)]
    return [build() for build in BUILT_IN_CASES.values()]
