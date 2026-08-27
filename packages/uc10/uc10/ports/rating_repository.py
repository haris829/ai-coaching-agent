from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from uc10.domain.models import RatingRecord


@runtime_checkable
class RatingRepository(Protocol):
    def save(self, rating: RatingRecord) -> RatingRecord:
        """Persist a rating. Raises ProviderUnavailable on a write failure."""
        ...

    def for_interaction(self, interaction_id: str) -> list[RatingRecord]:
        """Every rating ever recorded for an interaction, superseded ones included."""
        ...

    def supersede(self, rating_id: str, by: str) -> RatingRecord:
        """Mark a rating superseded. The record is retained, never deleted."""
        ...

    def current_in_window(self, window_start: datetime, window_end: datetime) -> list[RatingRecord]:
        """ASSUMED BY US (A-11): non-superseded ratings with ``rated_at`` in
        [window_start, window_end], across all users. Required by rolling-window flagging;
        the specification's port sketch did not include a read for it."""
        ...
