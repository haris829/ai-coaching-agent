"""In-memory ``DownloadLogRepository``.

Every export download is recorded against the session. The log is an append-only
sequence: one entry per download, never deduplicated, because the question it
answers is how many times the evidence was taken away, not whether it ever was.
"""

from __future__ import annotations

import threading

from uc09_summary.domain.models import DownloadEvent


class InMemoryDownloadLogRepository:
    """Process-local append-only download log."""

    @classmethod
    def from_settings(cls, settings: object) -> InMemoryDownloadLogRepository:
        return cls()

    def __init__(self) -> None:
        self._events: list[DownloadEvent] = []
        self._lock = threading.RLock()

    def record(self, event: DownloadEvent) -> None:
        with self._lock:
            self._events.append(event)

    def for_session(self, session_id: str) -> tuple[DownloadEvent, ...]:
        with self._lock:
            return tuple(e for e in self._events if e.session_id == session_id)

    def for_summary(self, summary_id: str) -> tuple[DownloadEvent, ...]:
        with self._lock:
            return tuple(e for e in self._events if e.summary_id == summary_id)

    def all_events(self) -> tuple[DownloadEvent, ...]:
        """Test-support: the whole log."""
        with self._lock:
            return tuple(self._events)
