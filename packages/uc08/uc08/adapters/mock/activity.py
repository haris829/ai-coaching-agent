"""Mock activity provider.

Serves an :class:`~uc08.adapters.mock.ledger.ActivityLedger`. Read-only by
shape: there is no method here that changes anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from uc08.adapters.mock.ledger import ActivityLedger, Fault
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import ActivityWindowRead, QuestionCountRead, TopicMention, TopicsRead
from uc08.domain.enums import SourceStatus
from uc08.domain.time_utils import ensure_utc
from uc08.ports.clock import Clock
from uc08.ports.upstream import ActivityProvider


class MockActivityProvider(ActivityProvider):
    """Deterministic in-process activity read model."""

    def __init__(self, clock: Clock, ledger: ActivityLedger | None = None, *, timeout_seconds: float = 5.0) -> None:
        self._clock = clock
        self._ledger = ledger if ledger is not None else ActivityLedger()
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> float:
        """The deadline this adapter honours, taken from configuration."""
        return self._timeout_seconds

    # -- reads --------------------------------------------------------------
    def last_activity_at(self, user_id: str) -> datetime | None:
        self._raise_configured_fault()
        interactions = self._ledger.for_user(user_id).interactions
        if not interactions:
            return None
        return max(interaction.occurred_at for interaction in interactions)

    def interactions_in_window(self, user_id: str, since: datetime) -> ActivityWindowRead:
        self._raise_configured_fault()
        boundary = ensure_utc(since)
        found = tuple(
            interaction
            for interaction in sorted(
                self._ledger.for_user(user_id).interactions, key=lambda item: item.occurred_at
            )
            if interaction.occurred_at >= boundary
        )
        status = SourceStatus.AVAILABLE if found else SourceStatus.EMPTY
        return ActivityWindowRead(interactions=found, status=status)

    def question_count(self, user_id: str) -> QuestionCountRead:
        self._raise_configured_fault()
        count = self._ledger.for_user(user_id).question_count
        if count is None:
            return QuestionCountRead(count=0, status=SourceStatus.EMPTY)
        if count < 0:
            raise ProviderInvalidResponse(self.port_name, "question count is not a non-negative integer")
        return QuestionCountRead(count=count, status=SourceStatus.AVAILABLE)

    def topics_in_window(self, user_id: str, since: datetime) -> TopicsRead:
        self._raise_configured_fault()
        boundary = ensure_utc(since)
        first_seen: dict[str, datetime] = {}
        for occurred_at, topic in sorted(self._ledger.for_user(user_id).topic_events, key=lambda item: item[0]):
            if occurred_at >= boundary:
                first_seen.setdefault(topic, occurred_at)
        mentions = tuple(
            TopicMention(name=name, first_mentioned_at=moment) for name, moment in first_seen.items()
        )
        status = SourceStatus.AVAILABLE if mentions else SourceStatus.EMPTY
        return TopicsRead(topics=mentions, status=status)

    # -- fault injection ----------------------------------------------------
    def _raise_configured_fault(self) -> None:
        fault = self._ledger.fault
        if fault == Fault.UNAVAILABLE:
            raise ProviderUnavailable(self.port_name, "activity read model did not answer")
        if fault == Fault.TIMEOUT:
            raise ProviderTimeout(self.port_name, f"deadline of {self._timeout_seconds}s exceeded")
        if fault == Fault.INVALID:
            raise ProviderInvalidResponse(self.port_name, "activity read model returned an unmappable payload")

    # -- conformance harness ------------------------------------------------
    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], ActivityProvider]]:
        """Named states the contract conformance suite drives this adapter into.

        Every adapter for this port must supply these keys; see
        ``tests/conformance/test_activity_provider_conformance.py``.
        """
        from uc08.adapters.mock import scenarios

        return scenarios.MOCK_ACTIVITY_SCENARIOS
