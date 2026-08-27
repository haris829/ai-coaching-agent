"""Persistence ports. These are the only ports this component writes through.

No ORM, no company schema. A repository stores and returns domain records; how
it does so is entirely behind the interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc09_summary.domain.models import DownloadEvent, SummaryRecord


@runtime_checkable
class SummaryRepository(Protocol):
    """Stores the summary records this component owns."""

    def save(self, summary: SummaryRecord) -> None:
        """Persist a summary, replacing any earlier record with the same id."""
        ...

    def get(self, summary_id: str) -> SummaryRecord | None:
        """Return a summary by id, or ``None``.

        Ownership is **not** checked here. Authorisation is an application-layer
        decision made against the resolved caller, so that a repository cannot
        accidentally become the place where a permission rule silently lives.
        """
        ...

    def for_session(self, session_id: str) -> tuple[SummaryRecord, ...]:
        """Return every summary generated for a session, newest first."""
        ...


@runtime_checkable
class DownloadLogRepository(Protocol):
    """Records export downloads against the session."""

    def record(self, event: DownloadEvent) -> None:
        """Append one download event. Called exactly once per download."""
        ...

    def for_session(self, session_id: str) -> tuple[DownloadEvent, ...]:
        """Return the download events recorded against a session, oldest first."""
        ...

    def for_summary(self, summary_id: str) -> tuple[DownloadEvent, ...]:
        """Return the download events recorded against a summary, oldest first."""
        ...
