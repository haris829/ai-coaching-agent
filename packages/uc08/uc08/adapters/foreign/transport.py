"""A fictional foreign upstream, in process.

This exists to prove replaceability. It is a **deliberately foreign** payload
family: different field names, different nesting, and different value
representations from the mock family.

  * account key           ``learnerRef``, not ``user_id``
  * interactions          nested at ``data.timeline.entries``
  * timestamps            epoch **milliseconds**, not ISO-8601 UTC
  * interaction id        ``eventKey``, not ``interaction_id``
  * question count        ``metrics.questionsAsked`` as a **string**
  * topics                ``data.timeline.entries[].subjectArea``
  * NARIC level           prose, e.g. ``"Level Six"``
  * completion            a percentage **string**, e.g. ``"64%"``

No URL is contacted. This is a stand-in for an upstream, not an invented API:
the point is that the adapter, and only the adapter, knows any of the above.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from uc08.domain.time_utils import ensure_utc


class ForeignFault:
    NONE = "none"
    #: transport refused the connection
    REFUSED = "refused"
    #: transport exceeded its deadline
    DEADLINE = "deadline"
    #: transport answered with a body the adapter cannot map
    GARBLED = "garbled"


class LexiconTransportRefused(RuntimeError):
    """Vendor-shaped failure. Must never escape the adapter."""


class LexiconDeadlineExceeded(RuntimeError):
    """Vendor-shaped timeout. Must never escape the adapter."""


def to_epoch_millis(moment: datetime) -> int:
    return int(ensure_utc(moment).timestamp() * 1000)


@dataclass
class LexiconTransport:
    """In-process stand-in for the foreign upstream."""

    learners: dict[str, dict[str, Any]] = field(default_factory=dict)
    fault: str = ForeignFault.NONE

    def learner(self, learner_ref: str) -> dict[str, Any]:
        return self.learners.setdefault(
            learner_ref,
            {"learnerRef": learner_ref, "data": {"timeline": {"entries": []}}, "metrics": {}},
        )

    def add_entry(self, learner_ref: str, occurred_at: datetime, event_key: str, subject_area: str | None) -> None:
        entry = {"eventKey": event_key, "ts": to_epoch_millis(occurred_at)}
        if subject_area is not None:
            entry["subjectArea"] = subject_area
        self.learner(learner_ref)["data"]["timeline"]["entries"].append(entry)

    def set_questions_asked(self, learner_ref: str, count: int | None) -> None:
        metrics = self.learner(learner_ref)["metrics"]
        if count is None:
            metrics.pop("questionsAsked", None)
        else:
            metrics["questionsAsked"] = str(count)

    def set_recommendation(self, learner_ref: str, recommendation: dict[str, Any] | None) -> None:
        self.learner(learner_ref)["payload"] = {"recommendation": recommendation}

    # -- transport behaviour ------------------------------------------------
    def fetch(self, learner_ref: str) -> dict[str, Any]:
        if self.fault == ForeignFault.REFUSED:
            raise LexiconTransportRefused("lexicon-edge-07: connection refused for tenant 4412")
        if self.fault == ForeignFault.DEADLINE:
            raise LexiconDeadlineExceeded("lexicon-edge-07: upstream read deadline exceeded after 5000ms")
        if self.fault == ForeignFault.GARBLED:
            return {"learnerRef": learner_ref, "data": {"timeline": "not-an-object"}}
        return self.learner(learner_ref)

    def with_fault(self, fault: str) -> LexiconTransport:
        self.fault = fault
        return self
