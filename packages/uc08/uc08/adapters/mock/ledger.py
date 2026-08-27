"""The data a mock upstream serves.

The ledger is deliberately **not** part of the adapter. ``ActivityProvider`` is
read-only by shape, so the mock adapter must not grow a seeding method; a test
builds a ledger, hands it to the adapter, and the adapter only ever reads it.

Nothing here is random and nothing sleeps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from uc08.domain.models import ActivityInteraction
from uc08.domain.time_utils import ensure_utc


class Fault:
    """Deterministic fault names a mock upstream can be put into."""

    NONE = "none"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID = "invalid"


@dataclass
class UserActivity:
    interactions: list[ActivityInteraction] = field(default_factory=list)
    #: ``None`` means the upstream has no count for this learner (empty, not zero).
    question_count: int | None = 0
    topic_events: list[tuple[datetime, str]] = field(default_factory=list)


@dataclass
class ActivityLedger:
    """What the mock activity read model contains."""

    users: dict[str, UserActivity] = field(default_factory=dict)
    fault: str = Fault.NONE

    def for_user(self, user_id: str) -> UserActivity:
        return self.users.setdefault(user_id, UserActivity())

    def add_interaction(
        self,
        user_id: str,
        occurred_at: datetime,
        interaction_id: str,
        *,
        topic: str | None = None,
    ) -> ActivityInteraction:
        moment = ensure_utc(occurred_at)
        interaction = ActivityInteraction(interaction_id=interaction_id, occurred_at=moment)
        entry = self.for_user(user_id)
        entry.interactions.append(interaction)
        if topic is not None:
            entry.topic_events.append((moment, topic))
        return interaction

    def set_question_count(self, user_id: str, count: int | None) -> None:
        self.for_user(user_id).question_count = count

    def add_topic(self, user_id: str, occurred_at: datetime, topic: str) -> None:
        self.for_user(user_id).topic_events.append((ensure_utc(occurred_at), topic))

    def with_fault(self, fault: str) -> ActivityLedger:
        self.fault = fault
        return self
