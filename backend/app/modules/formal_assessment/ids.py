"""Identifier generation, injected so tests get stable ids.

Generated ids are never what makes a record unique in UC-09. Every record this module owns has a
*natural* key that the persistence layer enforces, and each one exists to close a specific race:

=========================  ==============================================  =======================
Record                      Natural key                                     Closes
=========================  ==============================================  =======================
Formal attempt              ``(learner_id, quiz_id)`` among open states      Two concurrent starts
                            plus ``idempotency_key``                         becoming two attempts.
Formal attempt              ``attempt_id`` (the UC-03 attempt)               Two formal records
                                                                             wrapping one attempt.
Device session              ``formal_attempt_id`` among ACTIVE sessions      Two devices both
                                                                             becoming authoritative.
Formal review               ``formal_attempt_id``                            Two reviews, or two
                                                                             queue entries, for one
                                                                             passing attempt.
=========================  ==============================================  =======================

The generated ``formal_attempt_id`` / ``session_id`` / ``review_id`` exist so a client has a stable
handle to quote back, not to make anything unique.

A NOTE ON DEVICE IDENTIFIERS
----------------------------
``session_id`` is generated **here, on the server**, and returned to the client once. A browser-
generated device id is a claim, not a credential: anything the client computes, the client can
recompute on a second device. So the authoritative session is identified by a server-issued opaque
token, and the client's own device fingerprint is recorded as descriptive evidence only — useful to
an assessor, never load-bearing for the lock.
"""

from __future__ import annotations

import re
import secrets
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


#: Bytes of entropy in a session token. 32 bytes → 43 URL-safe characters.
SESSION_TOKEN_BYTES = 32


class TokenGenerator(Protocol):
    def __call__(self) -> str: ...


def secure_token() -> str:
    """A cryptographically random, URL-safe session token.

    Used for the authoritative device session. ``secrets`` rather than ``uuid4`` because this value
    is presented back as proof of holding the session lock: it must be unguessable, not merely
    unique.
    """
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


class SequentialIdGenerator:
    """Deterministic id generator for tests: ``uc09-0001``, ``uc09-0002``, …"""

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:04d}"


class SequentialTokenGenerator:
    """Deterministic session tokens for tests: ``token-0001``, ``token-0002``, …

    Only ever bound in tests. The production binding is :func:`secure_token`.
    """

    def __init__(self, prefix: str = "token") -> None:
        self._prefix = prefix
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:04d}"
