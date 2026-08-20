"""Idempotency keys (§20).

UC-03 makes attempt creation idempotent structurally — a partial unique index allows one open
attempt per learner per quiz. UC-05 and UC-08 derive their keys from stable domain identity rather
than asking a client for a token. UC-09 does both, and which one applies depends on whether the
operation has a natural key at all.

DERIVED, NO CLIENT TOKEN
------------------------
``formal-attempt:<learner_id>:<quiz_id>``
    One *open* formal attempt per learner and quiz. The persistence layer enforces uniqueness among
    open states only, so a learner who sat a formal assessment last month and is entitled to a
    retake gets a new record, while a duplicated "acknowledge conditions" call converges on the one
    they are in the middle of.

``formal-review:<formal_attempt_id>``
    One review per formal attempt, which is also what stops the same pending assessment being queued
    twice (§20): the queue entry is derived from the review, and there is only ever one review.

``formal-certificate:<formal_attempt_id>``
    One certificate workflow trigger per formal attempt. Passed to the certificate service so a
    provider that de-duplicates on its own side gets the chance to.

``formal-submission:<attempt_id>``
    One submission per attempt. Also passed to UC-03, whose own submission record is unique per
    attempt — belt and braces on the one operation that must never happen twice.

CLIENT-SUPPLIED, OPTIONAL
-------------------------
``session-registration:<formal_attempt_id>:<client_request_id>``
    The one place a derived key cannot work. Two registration requests for the same formal attempt
    are
    *usually* two devices — which must be refused — but occasionally one device retrying after a
    timeout, which must not be. Nothing about the request distinguishes them except a token only the
    original caller can have, so the client supplies one. Without it, the second registration is
    refused: the safe default is to protect the lock, not to guess.

    The token is namespaced to the formal attempt so a client reusing an obvious value such as
    ``"1"`` across two assessments cannot have one replay the other's session.
"""

from __future__ import annotations

#: Separator chosen because ids in this system are UUIDs or slugs, never containing ':'.
SEPARATOR = ":"

#: Bounded so a caller-supplied token cannot become an unbounded database key.
MAX_CLIENT_KEY_LENGTH = 128
#: A replay token that authorises reuse of a session must be unguessable, so a short one is refused
#: rather than accepted and quietly relied upon.
MIN_CLIENT_REQUEST_ID_LENGTH = 16


def _part(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("Idempotency key components must be non-empty.")
    return text


def formal_attempt_key(learner_id: str, quiz_id: str) -> str:
    """One open formal attempt per learner per quiz."""
    return SEPARATOR.join(("formal-attempt", _part(learner_id), _part(quiz_id)))


def review_key(formal_attempt_id: str) -> str:
    """One review — and therefore one queue entry — per formal attempt."""
    return SEPARATOR.join(("formal-review", _part(formal_attempt_id)))


def certificate_key(formal_attempt_id: str) -> str:
    """One certificate workflow trigger per formal attempt."""
    return SEPARATOR.join(("formal-certificate", _part(formal_attempt_id)))


def submission_key(attempt_id: str) -> str:
    """One submission per UC-03 attempt."""
    return SEPARATOR.join(("formal-submission", _part(attempt_id)))


def session_registration_key(formal_attempt_id: str, client_request_id: str) -> str:
    """Namespace a client's registration replay token to the formal attempt it applies to."""
    token = _part(client_request_id)
    if len(token) > MAX_CLIENT_KEY_LENGTH:
        raise ValueError(f"A client request id may be at most {MAX_CLIENT_KEY_LENGTH} characters.")
    if len(token) < MIN_CLIENT_REQUEST_ID_LENGTH:
        raise ValueError(
            f"A client request id must be at least {MIN_CLIENT_REQUEST_ID_LENGTH} characters so it "
            "cannot be guessed by a second device."
        )
    return SEPARATOR.join(("session-registration", _part(formal_attempt_id), token))


def is_usable_client_request_id(value: object) -> bool:
    """Whether a client-supplied replay token is long enough to be relied on."""
    return (
        isinstance(value, str)
        and MIN_CLIENT_REQUEST_ID_LENGTH <= len(value.strip()) <= MAX_CLIENT_KEY_LENGTH
    )
