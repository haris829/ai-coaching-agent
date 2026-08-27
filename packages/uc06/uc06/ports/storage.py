"""Persistence ports. Behind an interface; the shipped implementation is local
and in-process. No production database is used or assumed.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..domain.models import HaltRecord, InteractionRecord


@runtime_checkable
class InteractionLogRepository(Protocol):
    """Append-only interaction log.

    The record carries no question text and no case fact text - identifiers only.
    Reads are ownership-checked by the caller: no endpoint returns another user's
    interactions.
    """

    def append(self, record: InteractionRecord) -> None:
        ...

    def get(self, interaction_id: str) -> InteractionRecord | None:
        ...

    def list_for_session(self, session_id: str) -> Sequence[InteractionRecord]:
        ...


@runtime_checkable
class SessionHaltRepository(Protocol):
    """Halt state for case-linked coaching.

    A halt blocks every further case-linked response in that session until it is
    cleared. Clearing is an administrative act, not a learner action - see
    docs/assumptions.md row A-06, which flags the clearing procedure and the
    authorised role as unspecified by the company.
    """

    def halt(self, session_id: str, reason: str) -> None:
        ...

    def is_halted(self, session_id: str) -> bool:
        ...

    def clear(self, session_id: str) -> None:
        ...

    def get(self, session_id: str) -> HaltRecord:
        ...
