"""Mock gap report provider.

Its "upstream payload" is a small dict in this module own shape, mapped onto the
platform contract through :mod:`uc08.domain.naric` exactly as a real adapter
would. Read-only by shape.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from uc08.adapters.mock.ledger import Fault
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import Topic
from uc08.domain.naric import normalise_completion_percent, normalise_naric_level
from uc08.ports.clock import Clock
from uc08.ports.upstream import GapReportProvider


@dataclass
class GapReportPlan:
    """What the mock gap report will answer with.

    ``suggestion`` is a raw payload in the mock upstream own shape -- keys
    ``topic_id``, ``name``, ``naric_level``, ``course_progress_percent`` -- so
    that normalisation is genuinely exercised rather than bypassed.
    """

    suggestions: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    fault: str = Fault.NONE
    #: A payload the adapter cannot map at all (used by the invalid scenario).
    unmappable: bool = False

    def set_suggestion(self, user_id: str, payload: dict[str, Any] | None) -> None:
        self.suggestions[user_id] = payload

    def with_fault(self, fault: str) -> GapReportPlan:
        self.fault = fault
        return self


class MockGapReportProvider(GapReportProvider):
    def __init__(self, clock: Clock, plan: GapReportPlan | None = None, *, timeout_seconds: float = 5.0) -> None:
        self._clock = clock
        self._plan = plan if plan is not None else GapReportPlan()
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def suggested_topic(self, user_id: str) -> Topic | None:
        fault = self._plan.fault
        if fault == Fault.UNAVAILABLE:
            raise ProviderUnavailable(self.port_name, "gap report did not answer")
        if fault == Fault.TIMEOUT:
            raise ProviderTimeout(self.port_name, f"deadline of {self._timeout_seconds}s exceeded")
        if fault == Fault.INVALID:
            raise ProviderInvalidResponse(self.port_name, "gap report returned an unmappable payload")

        payload = self._plan.suggestions.get(user_id)
        if payload is None:
            # The report answered and had no suggestion. Nothing is invented.
            return None

        topic_id = payload.get("topic_id")
        name = payload.get("name")
        if not topic_id or not name:
            raise ProviderInvalidResponse(self.port_name, "suggestion is missing an identifier or a name")

        level = normalise_naric_level(payload.get("naric_level"), port=self.port_name)
        progress = normalise_completion_percent(payload.get("course_progress_percent"), port=self.port_name)
        return Topic(
            topic_id=str(topic_id),
            name=str(name),
            naric_level=level.level,
            naric_level_source=level.source,
            naric_level_status=level.status,
            explanation_profile=level.explanation_profile,
            course_progress_percent=progress.percent,
            course_progress_status=progress.status,
        )

    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], GapReportProvider]]:
        from uc08.adapters.mock import scenarios

        return scenarios.MOCK_GAP_REPORT_SCENARIOS
