"""Shared test support: the sensitive-text inventory and the question ledger.

The privacy guarantee is asserted across the WHOLE suite, so every question any
test sends is recorded here, and every fact text any mock or foreign upstream can
produce is enumerated here. tests/conftest.py scans the captured log output
against both at session end.
"""

from __future__ import annotations

from uc06.adapters.foreign import _upstream
from uc06.adapters.mock import case_file as mock_case_file
from uc06.adapters.real import newco_case_file

#: Every question text sent anywhere in the suite, recorded as it is sent.
_ASKED: list[str] = []


def record_question(question: str) -> str:
    _ASKED.append(question)
    return question


def asked_questions() -> tuple[str, ...]:
    return tuple(_ASKED)


def mock_fact_texts() -> tuple[str, ...]:
    """Fact text from every mock case file scenario."""
    texts: list[str] = []
    for builder in mock_case_file._CASES.values():
        case = builder()
        texts.extend(fact.text for fact in case.facts)
        texts.extend(charge.label for charge in case.charges)
        texts.extend(item.label for item in case.evidence)
        texts.extend(note.summary for note in case.legislation_notes)
    return tuple(t for t in texts if t)


def foreign_fact_texts() -> tuple[str, ...]:
    raw = _upstream.fetch_matter(_upstream.MATTER_STANDARD)
    record = raw["envelope"]["record"]
    return tuple(item["narrative"] for item in record["particulars"])


def demonstration_fact_texts() -> tuple[str, ...]:
    """Fact text from the swap-demonstration adapter, so the privacy scan covers
    every case-file source the suite can reach."""
    texts: list[str] = []
    for payload in newco_case_file._FIXTURES.values():
        texts.extend(str(point.get("body", "")) for point in payload.get("points", []))
    return tuple(t for t in texts if t)


def sensitive_texts() -> tuple[str, ...]:
    return mock_fact_texts() + foreign_fact_texts() + demonstration_fact_texts()


def scan_for_leaks(captured: str) -> list[str]:
    """Return a description of every sensitive string found in captured output.

    Matching is on a distinctive window of each string, so a leak of any
    substantial portion is caught even if the text was truncated on its way into
    a log line.
    """
    leaks: list[str] = []
    for text in sensitive_texts():
        for window in _windows(text):
            if window in captured:
                leaks.append(f"case fact text in log output: {window[:60]!r}")
                break
    for question in asked_questions():
        for window in _windows(question):
            if window in captured:
                leaks.append(f"question text in log output: {window[:60]!r}")
                break
    return leaks


def _windows(text: str, size: int = 40) -> tuple[str, ...]:
    """Distinctive slices of a string. Short strings are matched whole."""
    clean = " ".join(text.split())
    if len(clean) <= size:
        return (clean,) if len(clean) >= 12 else ()
    return (clean[:size], clean[len(clean) // 2 : len(clean) // 2 + size], clean[-size:])
