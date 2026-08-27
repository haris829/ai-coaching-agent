from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc10.domain.models import ContentReviewFlag


@runtime_checkable
class AdminNotificationSink(Protocol):
    def flag_created(self, flag: ContentReviewFlag) -> None:
        """Notify the platform team. The flag carries counts and identifiers only --
        never question, response or comment text."""
        ...
