"""Privacy helpers.

Question text is not persisted. The interaction record carries the field because the platform
contract defines it, but UC-04 writes a redaction marker rather than the learner's words: the
record is queried by concept and lesson, and the question adds re-identification risk without
adding analytical value.
"""

from __future__ import annotations

REDACTED = "[redacted:not_persisted]"


def redact_question(_question: str) -> str:
    """Always returns the marker. The argument exists so call sites read honestly."""
    return REDACTED
