"""The only write-capable port in UC-07.

``GapReportRepository`` persists reports UC-07 generated itself. Nothing else in
this service may write anywhere.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from uc07.domain.models import GapReport


class GapReportRepository(ABC):
    """Persistence for generated gap reports (UC-07 owned data only)."""

    @abstractmethod
    def save(self, report: GapReport) -> None:
        """Persist ``report`` as the learner's current report."""

    @abstractmethod
    def get_current(self, user_id: str) -> GapReport | None:
        """Return the stored current report for ``user_id``, or ``None``.

        Implementations MUST scope reads by ``user_id``: a caller may never
        receive another learner's report.
        """
