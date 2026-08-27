"""In-memory persistence. Lightweight local implementation, no ORM, no schema.

This is the default backend. It is deliberately small: the point of the
repository ports is that swapping this for the platform store is an adapter
change and nothing else.
"""

from __future__ import annotations

from threading import RLock

from uc08.domain.models import Badge, FreezeOffer, StreakRecord, WeeklySummary
from uc08.ports.repositories import (
    BadgeRepository,
    FreezeOfferRepository,
    ProcessedInteractionStore,
    StreakRepository,
    WeeklySummaryRepository,
)


class InMemoryStreakRepository(StreakRepository):
    def __init__(self) -> None:
        self._rows: dict[str, StreakRecord] = {}
        self._lock = RLock()

    def get(self, user_id: str) -> StreakRecord | None:
        with self._lock:
            return self._rows.get(user_id)

    def save(self, streak: StreakRecord) -> None:
        with self._lock:
            self._rows[streak.user_id] = streak


class InMemoryBadgeRepository(BadgeRepository):
    """Append-only. There is no removal method: badges are permanent."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[int, Badge]] = {}
        self._lock = RLock()

    def get_all(self, user_id: str) -> tuple[Badge, ...]:
        with self._lock:
            held = self._rows.get(user_id, {})
            return tuple(held[milestone] for milestone in sorted(held))

    def award(self, badge: Badge) -> None:
        with self._lock:
            held = self._rows.setdefault(badge.user_id, {})
            # Idempotent on (user_id, milestone): a repeat is a no-op, and the
            # original award timestamp is the one that stands.
            held.setdefault(badge.milestone, badge)


class InMemoryWeeklySummaryRepository(WeeklySummaryRepository):
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, WeeklySummary]] = {}
        self._lock = RLock()

    def save(self, summary: WeeklySummary) -> None:
        with self._lock:
            self._rows.setdefault(summary.user_id, {})[summary.week] = summary

    def get(self, user_id: str, week: str) -> WeeklySummary | None:
        with self._lock:
            return self._rows.get(user_id, {}).get(week)

    def list_for_user(self, user_id: str) -> tuple[WeeklySummary, ...]:
        with self._lock:
            rows = self._rows.get(user_id, {})
            return tuple(rows[week] for week in sorted(rows, reverse=True))


class InMemoryFreezeOfferRepository(FreezeOfferRepository):
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, FreezeOffer]] = {}
        self._order: dict[str, list[str]] = {}
        self._lock = RLock()

    def get_latest(self, user_id: str) -> FreezeOffer | None:
        with self._lock:
            order = self._order.get(user_id, [])
            if not order:
                return None
            return self._rows[user_id][order[-1]]

    def save(self, offer: FreezeOffer) -> None:
        with self._lock:
            rows = self._rows.setdefault(offer.user_id, {})
            if offer.offer_id not in rows:
                self._order.setdefault(offer.user_id, []).append(offer.offer_id)
            rows[offer.offer_id] = offer


class InMemoryProcessedInteractionStore(ProcessedInteractionStore):
    def __init__(self) -> None:
        self._rows: set[tuple[str, str]] = set()
        self._lock = RLock()

    def was_processed(self, user_id: str, interaction_id: str) -> bool:
        with self._lock:
            return (user_id, interaction_id) in self._rows

    def mark_processed(self, user_id: str, interaction_id: str) -> None:
        with self._lock:
            self._rows.add((user_id, interaction_id))
