"""Shared plumbing for the mock adapters.

Determinism rules for every mock in this package:

* Scenarios are selected explicitly (a default plus per-user overrides). Nothing
  is random, nothing depends on the wall clock.
* "Timeout" is modelled by awaiting an event that is never set, so the caller's
  ``asyncio.wait_for`` cancels it. There is no ``sleep``, so timeout tests run at
  the speed of the configured timeout and cannot flake.
* Every call is recorded so tests can assert provider invocation counts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Generic, TypeVar

#: Fixed base instant for all mock timestamps. Keeps fixtures byte-stable.
MOCK_EPOCH = datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)

ScenarioT = TypeVar("ScenarioT")


class RecordingMock(Generic[ScenarioT]):
    """Base class holding scenario selection and call recording."""

    def __init__(
        self,
        default_scenario: ScenarioT,
        overrides: dict[str, ScenarioT] | None = None,
    ) -> None:
        self.default_scenario = default_scenario
        self.overrides: dict[str, ScenarioT] = dict(overrides or {})
        self.calls: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def reset_calls(self) -> None:
        self.calls.clear()

    def scenario_for(self, user_id: str) -> ScenarioT:
        return self.overrides.get(user_id, self.default_scenario)

    def set_scenario(self, scenario: ScenarioT, user_id: str | None = None) -> None:
        if user_id is None:
            self.default_scenario = scenario
        else:
            self.overrides[user_id] = scenario

    def _record(self, user_id: str) -> ScenarioT:
        self.calls.append(user_id)
        return self.scenario_for(user_id)

    @staticmethod
    async def _hang() -> None:
        """Never returns. Cancelled by the caller's per-provider timeout."""
        await asyncio.Event().wait()
