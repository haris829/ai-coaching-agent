"""Mock learner-context provider.

Scenario coverage required by section 7 of the brief: every NARIC level;
retrieved vs default source; practice area present and absent; unavailable.
Plus timeout and the ``invalid`` case, which is the interesting one.

The ``invalid`` scenario models an upstream that returns a level UC-05 cannot
map -- "RQF Level 6 (Hons)", ``6``, ``"undergraduate"``.  The platform contract
prescribes exactly what happens: apply the default, mark the source
``default``, record the status ``invalid``, log it.  Note that this adapter
does **not** raise: an unmappable *level* is a recoverable normalisation
outcome with a prescribed result, unlike an unparseable *payload*, which is a
``ProviderInvalidResponse``.

This adapter is also the reference for the rule that an adapter never invents
data.  A missing practice area becomes ``None`` with status ``empty`` -- never
a plausible-looking guess.
"""

from __future__ import annotations

import asyncio

from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ...domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ...domain.models import LearnerContext
from ...domain.profiles import coerce_naric_level
from ...registry import LEARNER_CONTEXT_REGISTRY

PORT = "learner_context_provider"
SLOW_SECONDS = 30.0

#: The fictional upstream payloads this mock pretends to have received.  They
#: are *this adapter's* private business; nothing outside sees these shapes.
_UPSTREAM_BY_SCENARIO: dict[str, dict[str, object]] = {
    "level_3": {"naricLevel": "LEVEL_3", "provenance": "retrieved", "area": "Family"},
    "level_4": {"naricLevel": "LEVEL_4", "provenance": "retrieved", "area": "Crime"},
    "level_5": {"naricLevel": "LEVEL_5", "provenance": "retrieved", "area": "Contract"},
    "level_6": {"naricLevel": "LEVEL_6", "provenance": "retrieved", "area": "Employment"},
    "level_7": {"naricLevel": "LEVEL_7", "provenance": "retrieved", "area": "Tax"},
    "level_7_plus": {
        "naricLevel": "LEVEL_7_PLUS",
        "provenance": "retrieved",
        "area": "Public",
    },
    "no_practice_area": {
        "naricLevel": "LEVEL_6",
        "provenance": "retrieved",
        "area": None,
    },
    "defaulted_source": {
        "naricLevel": "LEVEL_5",
        "provenance": "default",
        "area": None,
    },
    "invalid_level": {
        "naricLevel": "RQF Level 6 (Hons)",
        "provenance": "retrieved",
        "area": "Employment",
    },
}


@LEARNER_CONTEXT_REGISTRY.register("mock")
class MockLearnerContextProvider:
    def __init__(
        self,
        scenario: str = "level_5",
        script: list[str] | None = None,
        **_: object,
    ) -> None:
        self.scenario = scenario
        self._script = list(script or [])
        self.calls = 0

    def _next(self) -> str:
        self.calls += 1
        if self._script:
            return self._script.pop(0)
        return self.scenario

    async def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        scenario = self._next()

        if scenario == "unavailable":
            raise ProviderUnavailable(PORT, "scripted outage")
        if scenario == "timeout":
            raise ProviderTimeout(PORT, "scripted timeout")
        if scenario == "malformed":
            # The upstream answered with something that cannot be turned into a
            # LearnerContext at all.  Distinct from ``invalid_level``, which is
            # a recoverable normalisation outcome with a prescribed result.
            raise ProviderInvalidResponse(PORT, "unparseable payload")
        if scenario == "slow":
            await asyncio.sleep(SLOW_SECONDS)
            scenario = "level_5"
        if scenario == "empty":
            # The source answered and had nothing.  Distinct from unavailable.
            return LearnerContext(
                naric_level=NaricLevel.LEVEL_5,
                naric_level_source=NaricLevelSource.DEFAULT,
                practice_area=None,
                source_status={
                    "naric_level": SourceStatus.EMPTY,
                    "practice_area": SourceStatus.EMPTY,
                },
            )

        payload = _UPSTREAM_BY_SCENARIO.get(scenario)
        if payload is None:
            raise ProviderUnavailable(PORT, f"unknown scenario {scenario!r}")

        return self._map(payload)

    @staticmethod
    def _map(payload: dict[str, object]) -> LearnerContext:
        """Upstream payload -> platform contract.  The only place that shape is known."""
        level, source, status = coerce_naric_level(payload.get("naricLevel"))
        if status is SourceStatus.AVAILABLE and payload.get("provenance") == "default":
            source, status = NaricLevelSource.DEFAULT, SourceStatus.PARTIAL

        area = payload.get("area")
        area_value = area if isinstance(area, str) and area.strip() else None
        return LearnerContext(
            naric_level=level,
            naric_level_source=source,
            practice_area=area_value,
            source_status={
                "naric_level": status,
                "practice_area": (
                    SourceStatus.AVAILABLE if area_value else SourceStatus.EMPTY
                ),
            },
        )
