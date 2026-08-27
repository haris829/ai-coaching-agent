"""Deterministic fault injection for persistence.

No randomness and no sleeping: a fault decorator fails the first N writes and
then behaves normally. This is what the write-failure scenarios in the mock
matrix are built from -- "write fails once then succeeds" and "write fails
twice".
"""

from __future__ import annotations

from uc08.domain.errors import RepositoryWriteFailed
from uc08.domain.models import StreakRecord
from uc08.ports.repositories import StreakRepository


class FaultyStreakRepository(StreakRepository):
    """Wrap a repository and fail its first ``fail_writes`` save calls.

    ``save_attempts`` and ``committed_writes`` are exposed so a test can assert
    that exactly one retry happened -- two attempts, not three, not one.
    """

    def __init__(self, inner: StreakRepository, *, fail_writes: int) -> None:
        if fail_writes < 0:
            raise ValueError("fail_writes must not be negative")
        self._inner = inner
        self._remaining_failures = fail_writes
        self.save_attempts = 0
        self.committed_writes = 0

    def get(self, user_id: str) -> StreakRecord | None:
        return self._inner.get(user_id)

    def save(self, streak: StreakRecord) -> None:
        self.save_attempts += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RepositoryWriteFailed(
                f"injected write failure (attempt {self.save_attempts}); no rows were committed"
            )
        self._inner.save(streak)
        self.committed_writes += 1
