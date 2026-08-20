"""Idempotency keys (§16).

UC-03 makes attempt creation idempotent structurally rather than by asking a client for a token:
a partial unique index allows one open attempt per learner per quiz, so a retried "start attempt"
cannot become a second attempt. UC-05 does the same for its downstream work by *deriving* every
key from stable domain identity. UC-08 follows both.

RETAKE CREATION — DERIVED, NO CLIENT TOKEN
------------------------------------------
::

    retake:<learner_id>:<quiz_id>:<previous_attempt_id>

A retake is always a retake *of* something, and a learner can retake a given attempt exactly
once — the next retake follows the attempt after it. So the previous attempt id is a natural key,
and a client that retries after a timeout produces the same key and converges on the same retake
without having had to remember anything. Nothing about duplicate prevention depends on frontend
behaviour, which §16 and §13 both require.

ADMINISTRATOR GRANTS — CLIENT-SUPPLIED, REQUIRED
------------------------------------------------
Grants are the one place a derived key would be wrong. Two identical grants to the same learner
can both be legitimate — an administrator may genuinely decide to add a second extra attempt a
week later — and no property of the domain distinguishes that from a double-submitted form. Only
the caller knows which one it meant, so ``POST /grants`` requires an idempotency key and the
namespacing below scopes it to the learner and quiz it was issued for. A replayed key returns the
stored grant; a fresh key creates a new one (§14).
"""

from __future__ import annotations

#: Separator chosen because ids in this system are UUIDs or slugs, never containing ':'.
SEPARATOR = ":"

#: Bounded so a caller-supplied token cannot become an unbounded database key.
MAX_CLIENT_KEY_LENGTH = 128


def _part(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("Idempotency key components must be non-empty.")
    return text


def retake_key(learner_id: str, quiz_id: str, previous_attempt_id: str) -> str:
    """One retake per learner per quiz per previous attempt."""
    return SEPARATOR.join(("retake", _part(learner_id), _part(quiz_id), _part(previous_attempt_id)))


def grant_key(learner_id: str, quiz_id: str, client_key: str) -> str:
    """Namespace a caller-supplied grant token to the learner and quiz it applies to.

    Scoping matters: without it, two administrators reusing an obvious token such as ``"1"`` for
    two different learners would collide, and the second learner would silently receive no grant
    while the API reported success.
    """
    token = _part(client_key)
    if len(token) > MAX_CLIENT_KEY_LENGTH:
        raise ValueError(
            f"An idempotency key may be at most {MAX_CLIENT_KEY_LENGTH} characters."
        )
    return SEPARATOR.join(("grant", _part(learner_id), _part(quiz_id), token))
