"""Fault-injecting decorators over the persistence ports.

These provide the specification's mock scenarios -- 'write fails', 'write fails then
succeeds' -- without any production adapter learning about failure injection.  Each
decorator satisfies exactly the same port as the repository it wraps, so it can be used
anywhere the real one can, including in the conformance suite.
"""

from __future__ import annotations

from datetime import datetime

from uc10.domain.flag_work import FlagWorkItem
from uc10.domain.flagging import FlagCandidate
from uc10.domain.models import ContentReviewFlag, RatingRecord
from uc10.domain.window import Window
from uc10.ports.errors import ProviderUnavailable
from uc10.ports.flag_repository import FlagRepository
from uc10.ports.flag_work_queue import FlagWorkQueue
from uc10.ports.rating_repository import RatingRepository


class _Fuse:
    """Fails the next ``times`` calls, then behaves normally."""

    def __init__(self, times: int, port: str, reason_code: str, retryable: bool = True) -> None:
        self.remaining = times
        self.attempts = 0
        self._port = port
        self._reason_code = reason_code
        self._retryable = retryable

    def check(self) -> None:
        self.attempts += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise ProviderUnavailable(self._port, self._reason_code, retryable=self._retryable)


class FailingRatingRepository:
    """RatingRepository whose first ``fail_saves`` writes fail."""

    def __init__(
        self,
        inner: RatingRepository,
        *,
        fail_saves: int = 0,
        fail_supersedes: int = 0,
        retryable: bool = True,
    ) -> None:
        self._inner = inner
        self.save_fuse = _Fuse(fail_saves, "RatingRepository", "write_failed", retryable)
        self.supersede_fuse = _Fuse(fail_supersedes, "RatingRepository", "write_failed", retryable)

    def save(self, rating: RatingRecord) -> RatingRecord:
        self.save_fuse.check()
        return self._inner.save(rating)

    def for_interaction(self, interaction_id: str) -> list[RatingRecord]:
        return self._inner.for_interaction(interaction_id)

    def supersede(self, rating_id: str, by: str) -> RatingRecord:
        self.supersede_fuse.check()
        return self._inner.supersede(rating_id, by)

    def current_in_window(
        self, window_start: datetime, window_end: datetime
    ) -> list[RatingRecord]:
        return self._inner.current_in_window(window_start, window_end)


class FailingFlagRepository:
    """FlagRepository whose first ``fail_writes`` saves/updates fail, then succeed."""

    def __init__(self, inner: FlagRepository, *, fail_writes: int = 1) -> None:
        self._inner = inner
        self.write_fuse = _Fuse(fail_writes, "FlagRepository", "write_failed")

    def save(self, flag: ContentReviewFlag) -> ContentReviewFlag:
        self.write_fuse.check()
        return self._inner.save(flag)

    def open_flag_for(self, topic_tag: str, window: Window) -> ContentReviewFlag | None:
        return self._inner.open_flag_for(topic_tag, window)

    def update(self, flag: ContentReviewFlag) -> ContentReviewFlag:
        self.write_fuse.check()
        return self._inner.update(flag)

    def list_open(self) -> list[ContentReviewFlag]:
        return self._inner.list_open()

    def get(self, flag_id: str) -> ContentReviewFlag:
        return self._inner.get(flag_id)


class FailingAdminNotificationSink:
    """AdminNotificationSink whose first ``fail_times`` notifications fail.

    A failed notification must never lose a flag that is already persisted.
    """

    def __init__(self, inner, *, fail_times: int = 1) -> None:
        self._inner = inner
        self.fuse = _Fuse(fail_times, "AdminNotificationSink", "notification_failed")

    def flag_created(self, flag: ContentReviewFlag) -> None:
        self.fuse.check()
        self._inner.flag_created(flag)

    @property
    def notified(self):
        return getattr(self._inner, "notified", [])


class FailingFlagWorkQueue:
    """FlagWorkQueue whose first ``fail_resolves`` resolutions fail.

    Models the worst case for the never-drop guarantee: the flag was written but the
    intent could not be closed. The intent stays pending and the next cycle finds the
    existing open flag and updates it, so a flag is duplicated by nothing and lost by
    nothing.
    """

    def __init__(self, inner: FlagWorkQueue, *, fail_resolves: int = 0) -> None:
        self._inner = inner
        self.resolve_fuse = _Fuse(fail_resolves, "FlagWorkQueue", "write_failed")

    def enqueue(self, candidate: FlagCandidate) -> FlagWorkItem:
        return self._inner.enqueue(candidate)

    def pending(self) -> list[FlagWorkItem]:
        return self._inner.pending()

    def pending_for_topic(self, topic_tag: str) -> FlagWorkItem | None:
        return self._inner.pending_for_topic(topic_tag)

    def mark_failed(self, work_id: str, reason_code: str) -> FlagWorkItem:
        return self._inner.mark_failed(work_id, reason_code)

    def resolve(self, work_id: str, flag_id: str) -> FlagWorkItem:
        self.resolve_fuse.check()
        return self._inner.resolve(work_id, flag_id)

    def update_candidate(self, work_id: str, candidate: FlagCandidate) -> FlagWorkItem:
        return self._inner.update_candidate(work_id, candidate)
