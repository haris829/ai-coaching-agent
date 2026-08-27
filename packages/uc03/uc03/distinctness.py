"""Paraphrase detection for the never-repeat-a-framing rule.

Selecting an unused `FramingStrategy` is necessary but not sufficient: a
generator can be told "use the analogy framing" and still return a reworded
version of what it said last time. This module measures how much new prose
overlaps with prose already shown for the same concept, so the service can
reject a paraphrase instead of passing it off as a fresh explanation.

The measure is deliberately simple and deterministic - a Jaccard overlap of
content-word sets. It is a guard against near-duplicates, not a semantic
similarity model.
"""

from __future__ import annotations

import re

#: Words too common to carry meaning about *how* something was explained.
_STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for
    with without by from as is are was were be been being it its they them their
    you your we our i he she his her which who whom what when where how why
    not no nor so such can could may might must shall should will would do does
    did done have has had having there here own same very just also more most
    other some any each few many much one two both all
    """.split()
)

#: Above this overlap, two explanations are treated as the same explanation.
DEFAULT_THRESHOLD = 0.60


def content_words(text: str) -> set[str]:
    tokens = re.findall(r"[a-z']+", text.lower())
    return {t for t in tokens if len(t) > 2 and t not in _STOPWORDS}


def overlap(first: str, second: str) -> float:
    """Jaccard overlap of the two texts' content words, 0.0 - 1.0."""
    a, b = content_words(first), content_words(second)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_paraphrase(
    candidate: str, previous: tuple[str, ...], *, threshold: float = DEFAULT_THRESHOLD
) -> bool:
    """True when `candidate` substantially repeats any of `previous`."""
    return any(overlap(candidate, earlier) >= threshold for earlier in previous)


def max_overlap(candidate: str, previous: tuple[str, ...]) -> float:
    return max((overlap(candidate, earlier) for earlier in previous), default=0.0)
