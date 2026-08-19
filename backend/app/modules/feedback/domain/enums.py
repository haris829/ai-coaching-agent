"""UC-06's vocabulary."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ReportStatus", "REPORT_STATUS_LABELS"]


class ReportStatus(StrEnum):
    """Lifecycle of one feedback report.

    ``PENDING`` means the score and the outcome exist but the report has not been assembled yet --
    which is the state a generation failure leaves behind, and the state a retry clears.
    """

    PENDING = "PENDING"
    #: Assembled and frozen. Immutable from here, by trigger as well as by service.
    GENERATED = "GENERATED"
    #: Assembly failed for a reason a retry will not fix on its own.
    FAILED = "FAILED"


REPORT_STATUS_LABELS: dict[ReportStatus, str] = {
    ReportStatus.PENDING: "Feedback pending",
    ReportStatus.GENERATED: "Feedback ready",
    ReportStatus.FAILED: "Feedback unavailable",
}
