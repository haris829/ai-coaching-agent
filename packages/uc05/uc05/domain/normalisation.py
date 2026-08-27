"""Deterministic text normalisation and loop detection.

Loop protection compares guiding questions on a **normalised, semantically
meaningful form**, not raw string equality -- a reworded repeat defeats raw
equality.  The method here is entirely deterministic and model-free, so loop
detection is testable without a generator, which is the point.

Method (A-LOOP-METHOD):

1.  Lower-case, flatten apostrophes, strip punctuation, collapse whitespace.
2.  Drop *question-frame* stopwords -- the scaffolding a guiding question is
    made of ("what", "do", "you", "think", "might", "consider", ...).  Two
    questions that differ only in their scaffolding are the same probe.
3.  Suffix-strip each surviving token to a crude stem ("requires"/"required"/
    "requirement" -> "requir").  No lemmatiser, no model, no data files.
4.  The resulting sorted token set is the question's **fingerprint**.
5.  A verbatim repeat produces an identical fingerprint.  A reworded repeat is
    caught by Jaccard similarity of the token sets at or above
    ``LOOP_SIMILARITY_THRESHOLD``.
"""

from __future__ import annotations

import re
import unicodedata

# A-LOOP-THRESHOLD: 0.8 was chosen so that a genuine rewording of the same
# probe is caught while a question that advances the reasoning is not.  See
# docs/assumptions.md for the risk if this is wrong in either direction.
LOOP_SIMILARITY_THRESHOLD = 0.8

_APOSTROPHES = dict.fromkeys(map(ord, "‘’ʼ'"), None)
_NON_WORD = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE = re.compile(r"\s+")

# Question-frame stopwords.  Deliberately generous: these are the words a
# guiding question is *built from* rather than the words that carry its probe.
QUESTION_FRAME_STOPWORDS: frozenset[str] = frozenset(
    (
        # articles, conjunctions, determiners
        "a an the and or but if then than that this these those there here "
        # copulas and auxiliaries
        "is are was were be been being am do does did doing done have has had "
        "having can could will would shall should may might must "
        # pronouns
        "i you we they he she it me us them your our their his her its my "
        # interrogatives
        "what which who whom whose when where why how "
        # prepositions
        "to of in on at by for from with without into onto about as over "
        "under "
        # intensifiers and negation
        "not no nor so such very just really quite rather actually "
        # the verbs a guiding question is framed with
        "think thinks thinking consider considering considers reflect "
        "reflecting tell say says said ask asking asks answer answering look "
        "looking see seeing seem seems happen happens happened "
        # scaffolding nouns
        "case cases question questions point points step steps "
        # sequencing and politeness
        "now next first second third please lets let "
    )
    .split()
)

_CLAUSE_PUNCTUATION = re.compile(r"[.,;:!?\n\r]+")
_CLAUSE_CONNECTIVES = re.compile(
    r"\s+(?:but|though|although|however|because|and then|so that)\s+"
)

_SUFFIXES = ("ational", "iveness", "fulness", "ousness", "ization", "isation",
             "ations", "ation", "ments", "ment", "ness", "able", "ible",
             "ings", "ing", "ies", "ied", "ers", "er", "est", "ed", "es", "s")


def flatten(text: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.translate(_APOSTROPHES).lower()
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", lowered)).strip()


def stem(token: str) -> str:
    """Crude, deterministic suffix stripping.  No dictionary, no model.

    The trailing-``e`` strip at the end is what makes "require" and "required"
    agree; without it a reworded repeat slips under the similarity threshold.
    """
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            token = token[: -len(suffix)]
            break
    if len(token) >= 5 and token.endswith("e"):
        token = token[:-1]
    return token


def content_tokens(text: str) -> frozenset[str]:
    """The semantically meaningful stems of ``text``."""
    return frozenset(
        stem(token)
        for token in flatten(text).split()
        if token not in QUESTION_FRAME_STOPWORDS and len(token) > 1
    )


def fingerprint(text: str) -> str:
    """Canonical, order-independent identity of a guiding question."""
    return "|".join(sorted(content_tokens(text)))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def similarity(a: str, b: str) -> float:
    return jaccard(content_tokens(a), content_tokens(b))


def is_repeat(
    candidate: str,
    previous: list[str],
    threshold: float = LOOP_SIMILARITY_THRESHOLD,
) -> tuple[bool, int | None, float]:
    """Is ``candidate`` a repeat of anything in ``previous``?

    Returns ``(repeat, index_of_matched_previous, best_similarity)``.  The
    index makes the decision inspectable in logs and tests: a reviewer can see
    *which* earlier question was repeated, not merely that one was.
    """
    candidate_tokens = content_tokens(candidate)
    best_index: int | None = None
    best_score = 0.0
    for index, earlier in enumerate(previous):
        score = jaccard(candidate_tokens, content_tokens(earlier))
        if score > best_score:
            best_score, best_index = score, index
    return (best_score >= threshold and best_index is not None), best_index, best_score


def clauses(message: str) -> list[str]:
    """Split a learner message into normalised clauses.

    Explicit-statement detection matches a phrase only against a whole clause,
    which is what keeps "I don't know if consideration applies here" (one
    clause, not a match) apart from "I don't know." (a match).
    """
    if not (message or "").strip():
        return []

    # Split the RAW message first: ``flatten`` removes the punctuation that
    # marks clause boundaries, so splitting after flattening would lose them.
    parts: list[str] = []
    for segment in _CLAUSE_PUNCTUATION.split(message):
        normalised = flatten(segment)
        if normalised:
            parts.extend(_CLAUSE_CONNECTIVES.split(normalised))

    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        candidate = part.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result
