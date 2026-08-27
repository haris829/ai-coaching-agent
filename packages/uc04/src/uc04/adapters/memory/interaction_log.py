"""In-memory InteractionLogRepository.

Lightweight and local, behind the port, with no ORM and no schema assumptions. A real
repository implements the same protocol.
"""

from __future__ import annotations

from ...domain.errors import ProviderUnavailable
from ...domain.models import FalsePositiveRecord, InteractionRecord

PORT = "interaction_log"


class InMemoryInteractionLog:
    name = "memory"

    def __init__(self) -> None:
        self._records: dict[str, InteractionRecord] = {}
        self._false_positives: list[FalsePositiveRecord] = []
        #: Test switch, not part of the port.
        self.always_fail = False

    def _guard(self) -> None:
        if self.always_fail:
            raise ProviderUnavailable(PORT, "interaction log unavailable")

    def append(self, record: InteractionRecord) -> None:
        self._guard()
        self._records[record.interaction_id] = record

    def get(self, interaction_id: str) -> InteractionRecord | None:
        self._guard()
        return self._records.get(interaction_id)

    def list_for_session(self, session_id: str) -> list[InteractionRecord]:
        self._guard()
        return [r for r in self._records.values() if r.session_id == session_id]

    def append_false_positive(self, record: FalsePositiveRecord) -> None:
        self._guard()
        self._false_positives.append(record)

    def list_false_positives(self, session_id: str | None = None) -> list[FalsePositiveRecord]:
        self._guard()
        return [r for r in self._false_positives if session_id is None or r.session_id == session_id]

    # ------------------------------------------------------------------ metrics helper
    def unclassified_rate(self) -> float:
        """Share of records tagged ``unclassified``. Logged as a metric, per the brief."""
        if not self._records:
            return 0.0
        unclassified = sum(1 for r in self._records.values() if r.concept_tag == "unclassified")
        return round(unclassified / len(self._records), 4)
