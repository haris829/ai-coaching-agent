"""Foreign-family gap report adapter.

Its upstream nests the suggestion at ``payload.recommendation``, names the
fields ``id`` and ``label``, spells the level in prose (``"Level Six"``) and
reports completion as a percentage string (``"64%"``). All of that knowledge
stops in this file; the service above sees a platform ``Topic``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from uc08.adapters.foreign.transport import (
    ForeignFault,
    LexiconDeadlineExceeded,
    LexiconTransport,
    LexiconTransportRefused,
)
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import Topic
from uc08.domain.naric import normalise_completion_percent, normalise_naric_level
from uc08.ports.clock import Clock
from uc08.ports.conformance import CONFORMANCE_USER_ID
from uc08.ports.upstream import GapReportProvider


class ForeignGapReportAdapter(GapReportProvider):
    def __init__(self, clock: Clock, transport: LexiconTransport | None = None, *, timeout_seconds: float = 5.0) -> None:
        self._clock = clock
        self._transport = transport if transport is not None else LexiconTransport()
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def suggested_topic(self, user_id: str) -> Topic | None:
        body = self._fetch(user_id)
        payload = body.get("payload")
        if payload is None:
            # No recommendation block at all: the report answered with nothing.
            return None
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(self.port_name, "gap report response shape is not usable")
        recommendation: Any = payload.get("recommendation")
        if recommendation is None:
            return None
        if not isinstance(recommendation, dict):
            raise ProviderInvalidResponse(self.port_name, "gap report suggestion shape is not usable")

        topic_id = recommendation.get("id")
        label = recommendation.get("label")
        if not topic_id or not label:
            raise ProviderInvalidResponse(self.port_name, "gap report suggestion is missing an identifier or a name")

        level = normalise_naric_level(recommendation.get("academicTier"), port=self.port_name)
        progress = normalise_completion_percent(recommendation.get("courseCompletion"), port=self.port_name)
        return Topic(
            topic_id=str(topic_id),
            name=str(label),
            naric_level=level.level,
            naric_level_source=level.source,
            naric_level_status=level.status,
            explanation_profile=level.explanation_profile,
            course_progress_percent=progress.percent,
            course_progress_status=progress.status,
        )

    def _fetch(self, user_id: str) -> dict[str, Any]:
        try:
            return self._transport.fetch(user_id)
        except LexiconDeadlineExceeded as exc:
            raise ProviderTimeout(self.port_name, f"deadline of {self._timeout_seconds}s exceeded") from exc
        except LexiconTransportRefused as exc:
            raise ProviderUnavailable(self.port_name, "gap report did not answer") from exc

    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], GapReportProvider]]:
        return _FOREIGN_GAP_REPORT_SCENARIOS


#: Same topic as the mock family, in the foreign shape and spelling.
_FOREIGN_RECOMMENDATION = {
    "id": "topic-solicitors-accounts",
    "label": "Solicitors Accounts Rules",
    "academicTier": "Level Six",
    "courseCompletion": "64%",
}


def _with_recommendation(clock: Clock, recommendation: dict[str, Any] | None) -> GapReportProvider:
    transport = LexiconTransport()
    transport.set_recommendation(CONFORMANCE_USER_ID, recommendation)
    return ForeignGapReportAdapter(clock, transport)


def _foreign_suggestion(clock: Clock) -> GapReportProvider:
    return _with_recommendation(clock, dict(_FOREIGN_RECOMMENDATION))


def _foreign_no_suggestion(clock: Clock) -> GapReportProvider:
    return _with_recommendation(clock, None)


def _foreign_unavailable(clock: Clock) -> GapReportProvider:
    return ForeignGapReportAdapter(clock, LexiconTransport().with_fault(ForeignFault.REFUSED))


def _foreign_timeout(clock: Clock) -> GapReportProvider:
    return ForeignGapReportAdapter(clock, LexiconTransport().with_fault(ForeignFault.DEADLINE))


def _foreign_invalid(clock: Clock) -> GapReportProvider:
    return _with_recommendation(clock, {"id": "", "label": ""})


def _foreign_unmappable_level(clock: Clock) -> GapReportProvider:
    return _with_recommendation(
        clock,
        {**_FOREIGN_RECOMMENDATION, "academicTier": "postgraduate-ish", "courseCompletion": 0.64},
    )


_FOREIGN_GAP_REPORT_SCENARIOS: Mapping[str, Callable[[Clock], GapReportProvider]] = {
    "available": _foreign_suggestion,
    "empty": _foreign_no_suggestion,
    "unavailable": _foreign_unavailable,
    "timeout": _foreign_timeout,
    "invalid": _foreign_invalid,
    "suggestion_available": _foreign_suggestion,
    "no_suggestion": _foreign_no_suggestion,
    "unmappable_level": _foreign_unmappable_level,
}
