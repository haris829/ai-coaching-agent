"""Persistence ports for the records UC-08 owns.

There is no ORM and no company schema here. Each port is an interface with a
lightweight local implementation in ``uc08/adapters/persistence``.

Writes raise :class:`~uc08.domain.errors.RepositoryWriteFailed`; reads raise
:class:`~uc08.domain.errors.RepositoryReadFailed`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from uc08.domain.models import Badge, FreezeOffer, StreakRecord, WeeklySummary


class StreakRepository(ABC):
    """The streak record, keyed by account."""

    @abstractmethod
    def get(self, user_id: str) -> StreakRecord | None:
        """Current record for the account, or ``None`` if it has never had one."""

    @abstractmethod
    def save(self, streak: StreakRecord) -> None:
        """Persist the record.

        On failure this raises ``RepositoryWriteFailed``. The caller retries
        once and then preserves the last known count. It never resets.
        """


class BadgeRepository(ABC):
    """Milestone badges. Append-only by design: there is no removal method,
    because badges are permanent and no code path in this component removes one.
    """

    @abstractmethod
    def get_all(self, user_id: str) -> tuple[Badge, ...]:
        """Every badge held by the account, ascending by milestone."""

    @abstractmethod
    def award(self, badge: Badge) -> None:
        """Persist a new badge.

        Implementations must be idempotent on ``(user_id, milestone)``: a
        repeated award for a milestone already held is a no-op, never a
        duplicate row.
        """


class WeeklySummaryRepository(ABC):
    @abstractmethod
    def save(self, summary: WeeklySummary) -> None:
        """Insert or replace the summary for ``(user_id, week)``."""

    @abstractmethod
    def get(self, user_id: str, week: str) -> WeeklySummary | None:
        """The summary for an ISO week key such as ``2026-W34``."""

    @abstractmethod
    def list_for_user(self, user_id: str) -> tuple[WeeklySummary, ...]:
        """Every summary for the account, most recent week first.

        Added beyond the specified port shape because ``GET
        /api/v1/weekly-summaries`` requires it (A-15).
        """


class FreezeOfferRepository(ABC):
    """Freeze offers. Added beyond the specified port list because the scope
    requires the offer to be modelled explicitly -- offered, accepted,
    declined, expired -- and that state has to live somewhere (A-10).
    """

    @abstractmethod
    def get_latest(self, user_id: str) -> FreezeOffer | None:
        """Most recently created offer for the account, whatever its status."""

    @abstractmethod
    def save(self, offer: FreezeOffer) -> None:
        """Insert or replace by ``offer_id``."""


class ProcessedInteractionStore(ABC):
    """Interaction ids already folded into the streak, for idempotency (A-05)."""

    @abstractmethod
    def was_processed(self, user_id: str, interaction_id: str) -> bool:
        ...

    @abstractmethod
    def mark_processed(self, user_id: str, interaction_id: str) -> None:
        ...
