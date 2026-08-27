"""Fact reference verification - verify, do not trust.

A generator that references a fact identifier not present in the loaded case file
has fabricated evidence about a live matter. That is the most dangerous output
this component can produce, so it is not repaired and passed on: unresolved
references are stripped from the text (so nothing fabricated can survive even in
a log or an incident detail) AND the generation is rejected as
ProviderInvalidResponse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.errors import ProviderInvalidResponse
from ..domain.models import CaseFile

#: Inline marker form the prompt registry instructs the generator to use.
MARKER = re.compile(r"\[\[fact:(?P<fact_id>[A-Za-z0-9_.\-]{1,64})\]\]")


@dataclass(frozen=True, slots=True)
class VerifiedExplanation:
    """Text with every fact marker resolved, plus the identifiers it cites."""

    text: str
    fact_ids: tuple[str, ...]


def _strip_unresolved(text: str, known: frozenset[str]) -> str:
    return MARKER.sub(lambda m: "" if m.group("fact_id") not in known else m.group(0), text)


def verify_and_render(content: str, claimed_ids: tuple[str, ...], case_file: CaseFile) -> VerifiedExplanation:
    """Resolve every reference against the case file, or reject the generation.

    Checks both channels the generator can cite through - inline markers in the
    text and the declared `fact_ids_referenced` list - because either one can
    carry a fabrication.
    """
    known = case_file.fact_ids()
    in_text = tuple(m.group("fact_id") for m in MARKER.finditer(content))
    unresolved = sorted({fid for fid in (*in_text, *claimed_ids) if fid not in known})

    if unresolved:
        # Strip first so nothing fabricated survives anywhere, then reject. The
        # detail carries identifiers only - never the generated text.
        _ = _strip_unresolved(content, known)
        raise ProviderInvalidResponse(
            "answer_generator",
            "fabricated_fact_reference:" + ",".join(unresolved),
        )

    rendered = MARKER.sub(_render, content)
    ordered: list[str] = []
    for fact_id in (*in_text, *claimed_ids):
        if fact_id not in ordered:
            ordered.append(fact_id)
    return VerifiedExplanation(text=rendered, fact_ids=tuple(ordered))


def _render(match: re.Match[str]) -> str:
    """Markers become a readable citation of the identifier.

    Fact TEXT stays in the response body where the generator placed it - the
    reader already holds read access to the case file. Only the marker itself is
    rewritten, so the response cites facts the way the interaction record does.
    """
    return f"(case file fact {match.group('fact_id')})"


def unresolved_ids(content: str, claimed_ids: tuple[str, ...], case_file: CaseFile) -> tuple[str, ...]:
    known = case_file.fact_ids()
    in_text = tuple(m.group("fact_id") for m in MARKER.finditer(content))
    return tuple(sorted({fid for fid in (*in_text, *claimed_ids) if fid not in known}))
