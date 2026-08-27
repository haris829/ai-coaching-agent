from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc10.domain.models import ContentReviewFlag
from uc10.domain.window import Window


@runtime_checkable
class FlagRepository(Protocol):
    def save(self, flag: ContentReviewFlag) -> ContentReviewFlag:
        """Persist a new flag. Raises ProviderUnavailable on a write failure."""
        ...

    def open_flag_for(self, topic_tag: str, window: Window) -> ContentReviewFlag | None:
        """The open flag for this topic whose window overlaps ``window``, if any.
        Used to update rather than re-raise -- see A-12."""
        ...

    def update(self, flag: ContentReviewFlag) -> ContentReviewFlag:
        """Replace an existing flag. Raises RecordNotFound if it does not exist."""
        ...

    def list_open(self) -> list[ContentReviewFlag]:
        ...

    def get(self, flag_id: str) -> ContentReviewFlag:
        """ASSUMED BY US (A-13): needed by the admin status-transition endpoint, which
        must be able to load a flag that is no longer open."""
        ...
