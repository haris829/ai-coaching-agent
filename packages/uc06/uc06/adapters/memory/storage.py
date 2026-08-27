"""In-process persistence. No production database.

Both repositories are behind ports, so replacing them with a real store is one
adapter file and one registry line. Nothing in the domain or application layer
knows these are dictionaries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Sequence

from ...config import Settings
from ...domain.models import HaltRecord, InteractionRecord


class InMemoryInteractionLogRepository:
    """Append-only. Records carry identifiers, never question or fact text."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._lock = RLock()
        self._records: list[InteractionRecord] = []

    def append(self, record: InteractionRecord) -> None:
        with self._lock:
            self._records.append(record)

    def get(self, interaction_id: str) -> InteractionRecord | None:
        with self._lock:
            for record in self._records:
                if record.interaction_id == interaction_id:
                    return record
        return None

    def list_for_session(self, session_id: str) -> Sequence[InteractionRecord]:
        with self._lock:
            return tuple(r for r in self._records if r.session_id == session_id)

    # Test/inspection helper. Not part of the port.
    def all_records(self) -> Sequence[InteractionRecord]:
        with self._lock:
            return tuple(self._records)


class InMemorySessionHaltRepository:
    """Halt state for case-linked coaching in a session."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._lock = RLock()
        self._halts: dict[str, HaltRecord] = {}

    def halt(self, session_id: str, reason: str) -> None:
        with self._lock:
            self._halts[session_id] = HaltRecord(
                session_id=session_id,
                halted=True,
                reason_code=reason,
                halted_at=datetime.now(timezone.utc),
            )

    def is_halted(self, session_id: str) -> bool:
        with self._lock:
            record = self._halts.get(session_id)
            return bool(record and record.halted)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._halts.pop(session_id, None)

    def get(self, session_id: str) -> HaltRecord:
        with self._lock:
            record = self._halts.get(session_id)
            if record is not None:
                return record
        return HaltRecord(session_id=session_id, halted=False, reason_code=None, halted_at=None)
