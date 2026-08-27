"""ASSUMED BY US (A-16): the durable retry queue behind 'never silently drop a flag'.

The specification requires that a failed flag write is retried on the next evaluation
cycle and that enough state is persisted for the retry to be possible.  It does not name
a port for that state, so this component defines one.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc10.domain.flag_work import FlagWorkItem
from uc10.domain.flagging import FlagCandidate


@runtime_checkable
class FlagWorkQueue(Protocol):
    def enqueue(self, candidate: FlagCandidate) -> FlagWorkItem:
        """Record the intent to write a flag, before any attempt is made."""
        ...

    def pending(self) -> list[FlagWorkItem]:
        """Unresolved intents, oldest first."""
        ...

    def pending_for_topic(self, topic_tag: str) -> FlagWorkItem | None:
        """The unresolved intent for a topic, if one is already queued."""
        ...

    def mark_failed(self, work_id: str, reason_code: str) -> FlagWorkItem:
        """Record a failed attempt. The item stays pending and is retried next cycle."""
        ...

    def resolve(self, work_id: str, flag_id: str) -> FlagWorkItem:
        """Called ONLY after the flag repository has confirmed the write."""
        ...

    def update_candidate(self, work_id: str, candidate: FlagCandidate) -> FlagWorkItem:
        ...
