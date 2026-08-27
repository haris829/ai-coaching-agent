"""A throwaway adapter used to prove that registration is one line.

It is registered at runtime by :mod:`tests.test_registry`, used, and
unregistered. No production module knows it exists - which is the point.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from uc09_summary.domain.enums import NaricLevel, NaricLevelSource, SessionStatus, SourceStatus
from uc09_summary.domain.models import SessionRecord


class SpareSessionProvider:
    """A minimal session adapter built only from the template contract."""

    @classmethod
    def from_settings(cls, settings: object) -> SpareSessionProvider:
        return cls()

    def get_session(self, session_id: str) -> SessionRecord:
        start = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
        return SessionRecord(
            session_id=session_id,
            user_id="spare-user",
            user_display_name="Spare Learner",
            started_at=start,
            ended_at=start + timedelta(minutes=20),
            status=SessionStatus.COMPLETED,
            naric_level=NaricLevel.LEVEL_5,
            naric_level_source=NaricLevelSource.RETRIEVED,
            naric_level_status=SourceStatus.AVAILABLE,
            course_completion_percent=10,
            course_title=None,
        )

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        return {"known_id": "spare-1", "expected_user_id": "spare-user"}


class ClockWithoutFactory:
    """Deliberately missing ``from_settings``, to prove the registry rejects it."""

    def now(self) -> datetime:  # pragma: no cover - never constructed
        return datetime.now(UTC)
