"""Explanation fingerprinting - the mechanism behind "a paraphrase is a repeat".

Two checks, both applied:

1. an exact hash of the canonicalised content-token stream, so reformatting or reordering an
   explanation cannot present it as new;
2. Jaccard similarity against every earlier attempt for the same concept in the same session,
   so a reworded but substantively identical explanation is rejected too.

The fingerprint is content-only: headings and framing labels are stripped by the tokeniser, so
"same explanation under a different heading" collides exactly as it should.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..domain.models import FramingAttempt
from .text import content_tokens, jaccard, stable_hash
from .thresholds import PARAPHRASE_SIMILARITY_THRESHOLD


@dataclass(frozen=True)
class Fingerprint:
    value: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class RepeatVerdict:
    is_repeat: bool
    #: "exact" when the hashes collide, "paraphrase" when similarity crossed the threshold.
    reason: str | None
    similarity: float


def fingerprint(text: str) -> Fingerprint:
    canonical = tuple(sorted(set(content_tokens(text))))
    return Fingerprint(value=stable_hash(" ".join(canonical)), tokens=canonical)


def is_repeat(
    candidate: Fingerprint,
    previous: Iterable[FramingAttempt],
    threshold: float = PARAPHRASE_SIMILARITY_THRESHOLD,
) -> RepeatVerdict:
    highest = 0.0
    for attempt in previous:
        if attempt.fingerprint == candidate.value:
            return RepeatVerdict(is_repeat=True, reason="exact", similarity=1.0)
        similarity = jaccard(candidate.tokens, attempt.fingerprint_tokens)
        highest = max(highest, similarity)
    if highest >= threshold:
        return RepeatVerdict(is_repeat=True, reason="paraphrase", similarity=round(highest, 4))
    return RepeatVerdict(is_repeat=False, reason=None, similarity=round(highest, 4))
