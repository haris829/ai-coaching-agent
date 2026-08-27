"""In-memory ``SummaryRepository``.

Lightweight local persistence behind the port. No ORM, no schema, no
assumption about the company database. Swapping in a real store is one adapter
file and one registry line; nothing above the port changes, because nothing
above the port knows how a summary is stored.

Records are stored as immutable domain models, so a caller holding a returned
record cannot mutate what the repository holds.
"""

from __future__ import annotations

import threading

from uc09_summary.domain.models import SummaryRecord


class InMemorySummaryRepository:
    """Process-local summary store, safe for concurrent request handling."""

    @classmethod
    def from_settings(cls, settings: object) -> InMemorySummaryRepository:
        return cls()

    def __init__(self) -> None:
        self._by_id: dict[str, SummaryRecord] = {}
        self._by_session: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    def save(self, summary: SummaryRecord) -> None:
        with self._lock:
            is_new = summary.summary_id not in self._by_id
            self._by_id[summary.summary_id] = summary
            if is_new:
                self._by_session.setdefault(summary.session_id, []).append(
                    summary.summary_id
                )

    def get(self, summary_id: str) -> SummaryRecord | None:
        with self._lock:
            return self._by_id.get(summary_id)

    def for_session(self, session_id: str) -> tuple[SummaryRecord, ...]:
        with self._lock:
            ids = list(self._by_session.get(session_id, ()))
            records = [self._by_id[i] for i in ids if i in self._by_id]
        records.sort(key=lambda r: (r.generated_at, r.summary_id), reverse=True)
        return tuple(records)
