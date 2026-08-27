"""Lightweight in-process persistence.

No production database, no ORM, no company schema assumptions.  Records are stored as
immutable domain objects in dictionaries; swapping this for a real store means writing
one new adapter that satisfies the same conformance suite.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from uc10.domain.enums import FlagStatus
from uc10.domain.flag_work import FlagWorkItem
from uc10.domain.flagging import FlagCandidate
from uc10.domain.ids import new_flag_work_id
from uc10.domain.models import ContentReviewFlag, RatingRecord
from uc10.domain.window import Window
from uc10.ports.errors import RecordNotFound


class InMemoryRatingRepository:
    """RatingRepository over a dict. Ratings are never deleted, only superseded."""

    def __init__(self) -> None:
        self._by_id: dict[str, RatingRecord] = {}
        self._lock = threading.RLock()

    def save(self, rating: RatingRecord) -> RatingRecord:
        with self._lock:
            self._by_id[rating.rating_id] = rating
            return rating

    def for_interaction(self, interaction_id: str) -> list[RatingRecord]:
        with self._lock:
            found = [r for r in self._by_id.values() if r.interaction_id == interaction_id]
        return sorted(found, key=lambda r: r.rated_at)

    def supersede(self, rating_id: str, by: str) -> RatingRecord:
        with self._lock:
            existing = self._by_id.get(rating_id)
            if existing is None:
                raise RecordNotFound("RatingRepository", "rating_not_found")
            updated = existing.superseded(by)
            self._by_id[rating_id] = updated
            return updated

    def current_in_window(
        self, window_start: datetime, window_end: datetime
    ) -> list[RatingRecord]:
        window = Window(start=window_start, end=window_end)
        with self._lock:
            found = [
                r for r in self._by_id.values() if r.is_current and window.contains(r.rated_at)
            ]
        return sorted(found, key=lambda r: r.rated_at)

    # -- inspection helpers used by tests; not part of the port ---------------

    def all_records(self) -> list[RatingRecord]:
        with self._lock:
            return sorted(self._by_id.values(), key=lambda r: r.rated_at)

    def count(self) -> int:
        with self._lock:
            return len(self._by_id)


class InMemoryFlagRepository:
    """FlagRepository over a dict."""

    def __init__(self) -> None:
        self._by_id: dict[str, ContentReviewFlag] = {}
        self._lock = threading.RLock()

    def save(self, flag: ContentReviewFlag) -> ContentReviewFlag:
        with self._lock:
            self._by_id[flag.flag_id] = flag
            return flag

    def open_flag_for(self, topic_tag: str, window: Window) -> ContentReviewFlag | None:
        with self._lock:
            candidates = [
                f
                for f in self._by_id.values()
                if f.topic_tag == topic_tag
                and f.status is FlagStatus.OPEN
                and Window(start=f.window_start, end=f.window_end).overlaps(window)
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda f: f.window_end)

    def update(self, flag: ContentReviewFlag) -> ContentReviewFlag:
        with self._lock:
            if flag.flag_id not in self._by_id:
                raise RecordNotFound("FlagRepository", "flag_not_found")
            self._by_id[flag.flag_id] = flag
            return flag

    def list_open(self) -> list[ContentReviewFlag]:
        with self._lock:
            found = [f for f in self._by_id.values() if f.is_open]
        return sorted(found, key=lambda f: f.created_at)

    def get(self, flag_id: str) -> ContentReviewFlag:
        with self._lock:
            flag = self._by_id.get(flag_id)
        if flag is None:
            raise RecordNotFound("FlagRepository", "flag_not_found")
        return flag

    # -- inspection helpers used by tests; not part of the port ---------------

    def all_flags(self) -> list[ContentReviewFlag]:
        with self._lock:
            return sorted(self._by_id.values(), key=lambda f: f.created_at)


class InMemoryFlagWorkQueue:
    """Durable-intent queue. An item leaves 'pending' only once a flag write succeeded."""

    def __init__(self, *, now_factory=None) -> None:
        self._items: dict[str, FlagWorkItem] = {}
        self._now = now_factory or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()

    def enqueue(self, candidate: FlagCandidate) -> FlagWorkItem:
        item = FlagWorkItem(
            work_id=new_flag_work_id(), candidate=candidate, enqueued_at=self._now()
        )
        with self._lock:
            self._items[item.work_id] = item
            return item

    def pending(self) -> list[FlagWorkItem]:
        with self._lock:
            found = [i for i in self._items.values() if i.is_pending]
        return sorted(found, key=lambda i: i.enqueued_at)

    def pending_for_topic(self, topic_tag: str) -> FlagWorkItem | None:
        for item in self.pending():
            if item.candidate.topic_tag == topic_tag:
                return item
        return None

    def mark_failed(self, work_id: str, reason_code: str) -> FlagWorkItem:
        with self._lock:
            item = self._require(work_id).failed(reason_code=reason_code)
            self._items[work_id] = item
            return item

    def resolve(self, work_id: str, flag_id: str) -> FlagWorkItem:
        with self._lock:
            item = self._require(work_id).resolved(flag_id=flag_id, at=self._now())
            self._items[work_id] = item
            return item

    def update_candidate(self, work_id: str, candidate: FlagCandidate) -> FlagWorkItem:
        with self._lock:
            item = self._require(work_id).with_candidate(candidate)
            self._items[work_id] = item
            return item

    def _require(self, work_id: str) -> FlagWorkItem:
        item = self._items.get(work_id)
        if item is None:
            raise RecordNotFound("FlagWorkQueue", "work_item_not_found")
        return item

    # -- inspection helper used by tests; not part of the port ----------------

    def all_items(self) -> list[FlagWorkItem]:
        with self._lock:
            return sorted(self._items.values(), key=lambda i: i.enqueued_at)
