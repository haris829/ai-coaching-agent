"""Deterministic text primitives shared by matching, tagging and fingerprinting.

Ported from the TypeScript reference. Two behaviours were re-verified rather than assumed,
because they do not translate byte-for-byte:

* ``\\b`` word-boundary semantics differ between the JS and Python regex engines around
  apostrophes and digits, so every pattern that relies on them is tested individually
  (see tests/test_regex_verification.py).
* The stemmer is deliberately conservative - no ``er``/``ed`` rules, which would turn
  "answer" into "answ" and silently change every downstream score.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if then than that this these those is are was were be been being am
    do does did doing have has had having i you he she it we they me him her us them my your
    his its our their to of in on at for with about as by from into over under again further
    so can could would should will shall may might must not no nor very just also too there
    here what which who whom when where why how please tell explain give some any all more
    most other such own same because
    """.split()
)

_PUNCT = re.compile(r"[^a-z0-9'\s-]")
#: Hyphens are split, not kept. "out-of-court" and "out of court" are the same phrase, and
#: treating them as different tokens made paraphrase detection and retrieval disagree with
#: themselves depending on how the author happened to punctuate.
_HYPHEN = re.compile(r"-+")
_WS = re.compile(r"\s+")
_SMART_QUOTES = re.compile("[‘’“”]")

#: Sentence splitter. Python's re supports fixed-width lookbehind, which covers this pattern.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: Conservative suffix rules, longest first.
_SUFFIXES: tuple[str, ...] = ("ations", "ation", "ings", "ing", "ies", "es", "s")


def normalize_text(value: str) -> str:
    lowered = _SMART_QUOTES.sub("'", value.lower())
    unhyphenated = _HYPHEN.sub(" ", lowered)
    return _WS.sub(" ", _PUNCT.sub(" ", unhyphenated)).strip()


def tokenize(value: str) -> list[str]:
    normalized = normalize_text(value)
    return [token for token in normalized.split(" ") if token] if normalized else []


def stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            trimmed = token[: -len(suffix)]
            return trimmed + "y" if suffix == "ies" else trimmed
    return token


def content_tokens(value: str) -> list[str]:
    return [stem(t) for t in tokenize(value) if len(t) > 2 and t not in STOPWORDS]


def unique_tokens(value: str) -> list[str]:
    seen: dict[str, None] = {}
    for token in content_tokens(value):
        seen.setdefault(token, None)
    return list(seen)


def jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    if not left and not right:
        return 1.0
    a, b = set(left), set(right)
    intersection = len(a & b)
    union = len(a) + len(b) - intersection
    return intersection / union if union else 0.0


def stable_hash(value: str) -> str:
    """FNV-1a 64-bit, hex. Stable across processes and runtimes."""
    h = 0xCBF29CE484222325
    for char in value:
        h ^= ord(char)
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return format(h, "016x")


def sentences(value: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(value) if s.strip()]


def truncate_words(value: str, max_words: int) -> str:
    words = value.split()
    if len(words) <= max_words:
        return value.strip()
    return " ".join(words[:max_words]) + "..."


def contains_all(haystack: Iterable[str], needles: Sequence[str]) -> bool:
    pool = set(haystack)
    return bool(needles) and all(n in pool for n in needles)
