"""Identifier generation for UC-07, injected so tests get stable ids.

UC-03 has an ``ids.py`` of its own for the same reason: a capability mints its own primary keys and
must not reach into another one to do it.

The generated ``session_id`` is *not* what makes a coaching session unique. The natural key is
``(learner_id, attempt_id, question_id)`` — one coaching session per learner per incorrect question
— and that key, enforced by a unique constraint, is what stops a repeated "start coaching" request
from opening a second session.
"""

from __future__ import annotations

import uuid
from typing import Protocol


class IdGenerator(Protocol):
    def __call__(self) -> str: ...


def uuid_generator() -> str:
    return str(uuid.uuid4())


class SequentialIdGenerator:
    """Deterministic id generator for tests: ``session-0001``, ``session-0002``, …

    Lives beside the real one, as ``app.core.time.FixedClock`` lives beside ``SystemClock``: a
    deterministic double belongs with the thing it stands in for, where a reader will find it.
    """

    __slots__ = ("_prefix", "_counter")

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:04d}"
