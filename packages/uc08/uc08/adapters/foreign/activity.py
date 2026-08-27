"""Foreign-family activity adapter.

Maps the :mod:`uc08.adapters.foreign.transport` payload family onto the platform
contract. This file is the only place in the repository that knows a timestamp
can arrive as epoch milliseconds or that a question count can arrive as a
string. Nothing above the port learns it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from uc08.adapters.foreign.transport import (
    ForeignFault,
    LexiconDeadlineExceeded,
    LexiconTransport,
    LexiconTransportRefused,
)
from uc08.domain.enums import SourceStatus
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import (
    ActivityInteraction,
    ActivityWindowRead,
    QuestionCountRead,
    TopicMention,
    TopicsRead,
)
from uc08.domain.time_utils import ensure_utc
from uc08.ports.clock import Clock
from uc08.ports.conformance import CONFORMANCE_USER_ID
from uc08.ports.upstream import ActivityProvider


class ForeignActivityAdapter(ActivityProvider):
    """Read-only adapter over the foreign learner timeline."""

    def __init__(self, clock: Clock, transport: LexiconTransport | None = None, *, timeout_seconds: float = 5.0) -> None:
        self._clock = clock
        self._transport = transport if transport is not None else LexiconTransport()
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    # -- reads --------------------------------------------------------------
    def last_activity_at(self, user_id: str) -> datetime | None:
        entries = self._entries(user_id)
        if not entries:
            return None
        return max(moment for moment, _key, _subject in entries)

    def interactions_in_window(self, user_id: str, since: datetime) -> ActivityWindowRead:
        boundary = ensure_utc(since)
        found = tuple(
            ActivityInteraction(interaction_id=key, occurred_at=moment)
            for moment, key, _subject in sorted(self._entries(user_id), key=lambda item: item[0])
            if moment >= boundary
        )
        return ActivityWindowRead(
            interactions=found,
            status=SourceStatus.AVAILABLE if found else SourceStatus.EMPTY,
        )

    def question_count(self, user_id: str) -> QuestionCountRead:
        body = self._fetch(user_id)
        metrics = body.get("metrics")
        if not isinstance(metrics, dict):
            raise ProviderInvalidResponse(self.port_name, "activity response shape is not usable")
        if "questionsAsked" not in metrics:
            return QuestionCountRead(count=0, status=SourceStatus.EMPTY)
        raw = metrics["questionsAsked"]
        try:
            count = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(self.port_name, "question count is not an integer") from exc
        if count < 0:
            raise ProviderInvalidResponse(self.port_name, "question count is negative")
        return QuestionCountRead(count=count, status=SourceStatus.AVAILABLE)

    def topics_in_window(self, user_id: str, since: datetime) -> TopicsRead:
        boundary = ensure_utc(since)
        first_seen: dict[str, datetime] = {}
        for moment, _key, subject in sorted(self._entries(user_id), key=lambda item: item[0]):
            if moment >= boundary and subject:
                first_seen.setdefault(subject, moment)
        mentions = tuple(
            TopicMention(name=name, first_mentioned_at=moment) for name, moment in first_seen.items()
        )
        return TopicsRead(
            topics=mentions,
            status=SourceStatus.AVAILABLE if mentions else SourceStatus.EMPTY,
        )

    # -- mapping ------------------------------------------------------------
    def _fetch(self, user_id: str) -> dict[str, Any]:
        """Fetch and translate transport failures into contract errors.

        The vendor exception types, the vendor node id and the vendor error text
        all stop here.
        """
        try:
            return self._transport.fetch(user_id)
        except LexiconDeadlineExceeded as exc:
            raise ProviderTimeout(self.port_name, f"deadline of {self._timeout_seconds}s exceeded") from exc
        except LexiconTransportRefused as exc:
            raise ProviderUnavailable(self.port_name, "activity read model did not answer") from exc

    def _entries(self, user_id: str) -> list[tuple[datetime, str, str | None]]:
        body = self._fetch(user_id)
        data = body.get("data")
        timeline = data.get("timeline") if isinstance(data, dict) else None
        if not isinstance(timeline, dict):
            raise ProviderInvalidResponse(self.port_name, "activity response shape is not usable")
        entries = timeline.get("entries")
        if not isinstance(entries, list):
            raise ProviderInvalidResponse(self.port_name, "activity collection shape is not usable")

        translated: list[tuple[datetime, str, str | None]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ProviderInvalidResponse(self.port_name, "activity entry is not an object")
            key = entry.get("eventKey")
            raw_ts = entry.get("ts")
            if not key or raw_ts is None:
                raise ProviderInvalidResponse(self.port_name, "activity entry is missing an identifier or a timestamp")
            try:
                moment = datetime.fromtimestamp(int(raw_ts) / 1000, tz=timezone.utc)
            except (TypeError, ValueError, OverflowError, OSError) as exc:
                raise ProviderInvalidResponse(self.port_name, "activity entry timestamp is not usable") from exc
            translated.append((moment, str(key), entry.get("subjectArea")))
        return translated

    # -- conformance harness ------------------------------------------------
    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], ActivityProvider]]:
        return _FOREIGN_ACTIVITY_SCENARIOS


# --------------------------------------------------------------------------
# Foreign-family scenarios, behaviourally equivalent to the mock family
# --------------------------------------------------------------------------
def _transport_with_prior(
    clock: Clock,
    *,
    hours: int = 0,
    minutes: int = 0,
    question_count: int | None = 1,
    subject: str = "professional-conduct",
) -> LexiconTransport:
    transport = LexiconTransport()
    transport.add_entry(
        CONFORMANCE_USER_ID,
        clock.now() - timedelta(hours=hours, minutes=minutes),
        f"prior-{hours}h{minutes:02d}m",
        subject,
    )
    transport.set_questions_asked(CONFORMANCE_USER_ID, question_count)
    return transport


def _build(clock: Clock, transport: LexiconTransport) -> ActivityProvider:
    return ForeignActivityAdapter(clock, transport)


def _foreign_23h59m(clock: Clock) -> ActivityProvider:
    return _build(clock, _transport_with_prior(clock, hours=23, minutes=59))


def _foreign_24h01m(clock: Clock) -> ActivityProvider:
    return _build(clock, _transport_with_prior(clock, hours=24, minutes=1))


def _foreign_same_day(clock: Clock) -> ActivityProvider:
    transport = LexiconTransport()
    for index in range(12):
        transport.add_entry(
            CONFORMANCE_USER_ID,
            clock.now() - timedelta(minutes=5 * (index + 1)),
            f"same-day-{index}",
            "wills-and-probate",
        )
    transport.set_questions_asked(CONFORMANCE_USER_ID, 12)
    return _build(clock, transport)


def _foreign_no_activity(clock: Clock) -> ActivityProvider:
    transport = LexiconTransport()
    transport.learner(CONFORMANCE_USER_ID)
    transport.set_questions_asked(CONFORMANCE_USER_ID, None)
    return _build(clock, transport)


def _foreign_unavailable(clock: Clock) -> ActivityProvider:
    return _build(clock, LexiconTransport().with_fault(ForeignFault.REFUSED))


def _foreign_timeout(clock: Clock) -> ActivityProvider:
    return _build(clock, LexiconTransport().with_fault(ForeignFault.DEADLINE))


def _foreign_invalid(clock: Clock) -> ActivityProvider:
    return _build(clock, LexiconTransport().with_fault(ForeignFault.GARBLED))


def _foreign_count(count: int) -> Callable[[Clock], ActivityProvider]:
    def build(clock: Clock) -> ActivityProvider:
        return _build(clock, _transport_with_prior(clock, hours=1, question_count=count))

    return build


_FOREIGN_ACTIVITY_SCENARIOS: Mapping[str, Callable[[Clock], ActivityProvider]] = {
    "available": _foreign_23h59m,
    "empty": _foreign_no_activity,
    "unavailable": _foreign_unavailable,
    "timeout": _foreign_timeout,
    "invalid": _foreign_invalid,
    "activity_23h59m_ago": _foreign_23h59m,
    "activity_24h01m_ago": _foreign_24h01m,
    "multiple_interactions_same_day": _foreign_same_day,
    "no_activity": _foreign_no_activity,
    **{f"question_count_{count}": _foreign_count(count) for count in (9, 10, 11, 49, 50, 99, 100, 150)},
}
