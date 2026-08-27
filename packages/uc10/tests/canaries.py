"""Canary strings.

Every piece of learner content anywhere in the test suite -- mock question text, mock
response text, every comment a test submits -- contains one of these fragments.  A
session-wide autouse fixture fails any test whose captured log output contains one, so
"no question, response or comment text in any log" is asserted across the whole suite
rather than in one place.
"""

from __future__ import annotations

CANARY_FRAGMENTS: tuple[str, ...] = (
    "MOCK_QUESTION_TEXT_DO_NOT_LOG",
    "MOCK_RESPONSE_TEXT_DO_NOT_LOG",
    "FOREIGN_QUESTION_TEXT_DO_NOT_LOG",
    "FOREIGN_RESPONSE_TEXT_DO_NOT_LOG",
    "CANARY_COMMENT_TEXT_DO_NOT_LOG",
)

COMMENT_CANARY = "CANARY_COMMENT_TEXT_DO_NOT_LOG"


def canary_comment(detail: str = "") -> str:
    """A learner comment that must never reach a log line."""
    return f"{COMMENT_CANARY} the coaching here quoted my client matter {detail}".strip()


def contains_canary(text: str) -> list[str]:
    return [fragment for fragment in CANARY_FRAGMENTS if fragment in text]
