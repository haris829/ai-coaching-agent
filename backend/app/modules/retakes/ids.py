"""Identifier generation for UC-08, injected so tests get stable ids.

Generated ids are never what makes a record unique in UC-08. Every record this module owns has a
*natural* key that the persistence layer enforces:

=====================  =============================================  ==========================
Record                  Natural key                                    Guarantees
=====================  =============================================  ==========================
Retake request          ``idempotency_key`` = learner + quiz +          A replayed request
                        previous attempt                                resolves to one retake.
Reserved attempt slot   ``(learner_id, quiz_id, attempt_number)``       Two concurrent retakes
                                                                        cannot take one slot.
Additional attempt      caller-supplied ``idempotency_key``             A retried grant does not
grant                                                                   grant twice.
=====================  =============================================  ==========================

The generated ``retake_id`` / ``grant_id`` exist so a client has a stable handle to quote back,
not to make anything unique.
"""

from __future__ import annotations

import re
from typing import Protocol
from uuid import uuid4


class IdGenerator(Protocol):
    def __call__(self) -> str: ...


def uuid_generator() -> str:
    return str(uuid4())


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def is_uuid(value: object) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


class SequentialIdGenerator:
    """Deterministic id generator for tests: ``retake-0001``, ``retake-0002``, …"""

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:04d}"
