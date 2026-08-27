"""Citation guard - the last line of defence for requirement 5.

The primary defence is structural: `GenerationRequest` carries no authority
data and `GeneratedProse` has no authority field, so the generator cannot write
the Authority Reference section at all. But a generator (especially an LLM one)
can still *mention* a case or statute inside prose. This module scans the three
prose parts for citation-shaped text and redacts anything that is not one of
the citations the LegalAuthorityProvider actually verified.

Redaction, not rejection, is the default: the learner still gets the
explanation, minus the unverifiable claim, plus a pointer to verify. Every
redaction is counted and lands on the log record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Each pattern matches a shape that asserts legal authority.
_CITATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Neutral citations: [2019] UKSC 12, [1932] UKHL 100, (1932) AC 562
    re.compile(r"[\[(]\s*\d{4}\s*[\])]\s*[A-Z][A-Za-z.]{1,10}(?:\s+\d+)?"),
    # Case names: Donoghue v Stevenson, R v Brown, DPP v Smith.
    # Party names may be a single capital letter ("R v Brown"), so the trailing
    # character class is `*`, not `+`.
    re.compile(r"\b[A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*)*\s+v\.?\s+[A-Z][A-Za-z'\-]*"),
    # Named statutes: Sale of Goods Act 1979, Human Rights Act 1998
    re.compile(
        r"\b[A-Z][A-Za-z'\-]+(?:\s+(?:[A-Z][A-Za-z'\-]+|of|and|the|for|to))*"
        r"\s+(?:Act|Regulations|Order|Rules)\s+\d{4}\b"
    ),
    # Section references: s 12, s. 12(1), section 15, art 6
    re.compile(
        r"\b(?:s\.?|section|sched(?:ule)?\.?|art(?:icle)?\.?)\s*\d+[A-Za-z]?(?:\(\d+\))*",
        re.I,
    ),
    # EU instruments
    re.compile(r"\b(?:Directive|Regulation)\s*\(?(?:EU|EC)\)?\s*(?:No\.?\s*)?\d+/\d+\b"),
    # Bare URLs
    re.compile(r"https?://\S+|www\.\S+"),
)

REDACTION = "[citation removed - not verified]"


@dataclass(frozen=True)
class GuardResult:
    text: str
    violations: int


def _allowed_spans(text: str, allowed: tuple[str, ...]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for phrase in allowed:
        phrase = phrase.strip()
        if not phrase:
            continue
        for m in re.finditer(re.escape(phrase), text):
            spans.append(m.span())
    return spans


def scrub(text: str, *, allowed_citations: tuple[str, ...] = ()) -> GuardResult:
    """Redact citation-shaped text that isn't in `allowed_citations`.

    `allowed_citations` are the exact strings a LegalAuthorityProvider verified
    for this answer; prose may repeat those verbatim.
    """
    if not text:
        return GuardResult(text=text, violations=0)

    allowed = _allowed_spans(text, allowed_citations)

    def _inside_allowed(start: int, end: int) -> bool:
        return any(a_start <= start and end <= a_end for a_start, a_end in allowed)

    matches: list[tuple[int, int]] = []
    for pattern in _CITATION_PATTERNS:
        for m in pattern.finditer(text):
            if not _inside_allowed(m.start(), m.end()):
                matches.append(m.span())

    if not matches:
        return GuardResult(text=text, violations=0)

    # Merge overlapping/adjacent spans so one citation counts once.
    matches.sort()
    merged: list[list[int]] = []
    for start, end in matches:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    out: list[str] = []
    cursor = 0
    for start, end in merged:
        out.append(text[cursor:start])
        out.append(REDACTION)
        cursor = end
    out.append(text[cursor:])
    cleaned = re.sub(r"\s{2,}", " ", "".join(out)).strip()
    return GuardResult(text=cleaned, violations=len(merged))


def contains_citation(text: str) -> bool:
    """Detection helper used by tests and by adapter self-checks."""
    return any(p.search(text) for p in _CITATION_PATTERNS)
