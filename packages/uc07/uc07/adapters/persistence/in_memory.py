"""In-memory gap-report repository.

Lightweight local implementation of the only write-capable port. There is no
production database in UC-07: a real deployment swaps this adapter, not the
service.

Reads are scoped by ``user_id``, so one learner can never receive another
learner's report even if a caller asks for it.
"""

from __future__ import annotations

from threading import RLock

from uc07.domain.models import GapReport
from uc07.ports.persistence import GapReportRepository


class InMemoryGapReportRepository(GapReportRepository):
    def __init__(self) -> None:
        self._current: dict[str, GapReport] = {}
        self._history: dict[str, list[GapReport]] = {}
        self._lock = RLock()

    def save(self, report: GapReport) -> None:
        with self._lock:
            self._current[report.user_id] = report
            self._history.setdefault(report.user_id, []).append(report)

    def get_current(self, user_id: str) -> GapReport | None:
        with self._lock:
            report = self._current.get(user_id)
        if report is None:
            return None
        if report.user_id != user_id:  # pragma: no cover - defensive
            return None
        return report

    # -- local inspection helpers (not part of the port) --------------------

    def saved_count(self, user_id: str) -> int:
        with self._lock:
            return len(self._history.get(user_id, []))
