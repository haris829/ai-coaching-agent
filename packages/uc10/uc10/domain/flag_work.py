"""Durable intent-to-flag.

A flag is *decided* by a pure rule and *written* by an adapter that can fail.  Between
those two moments the intent is persisted, so a write failure leaves work behind rather
than losing a flag.  Dropping a flag is structurally impossible: the flag is only removed
from the queue after the repository has confirmed the write.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from uc10.domain.flagging import FlagCandidate


@dataclass(frozen=True, slots=True)
class FlagWorkItem:
    work_id: str
    candidate: FlagCandidate
    enqueued_at: datetime
    attempts: int = 0
    last_reason_code: str | None = None
    resolved_at: datetime | None = None
    resolved_flag_id: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.resolved_at is None

    def failed(self, *, reason_code: str) -> FlagWorkItem:
        return replace(self, attempts=self.attempts + 1, last_reason_code=reason_code)

    def resolved(self, *, flag_id: str, at: datetime) -> FlagWorkItem:
        return replace(
            self, attempts=self.attempts + 1, resolved_at=at, resolved_flag_id=flag_id
        )

    def with_candidate(self, candidate: FlagCandidate) -> FlagWorkItem:
        """Refresh a pending intent with newer counts for the same topic."""
        return replace(self, candidate=candidate)
