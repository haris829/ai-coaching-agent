"""The canonical educational disclaimer.

Single source of truth. Defined here exactly once, as a module-level constant.

The string literal below is the ONLY occurrence of this text in non-test source.
Everything that needs the disclaimer imports this constant. A second copy is a
second thing that can drift, and this text is verified by an automated scan.

SPEC DISCREPANCY (flagged, unresolved - requires company confirmation):
    The scope document states the disclaimer twice with different wording.
    The Overview declares the canonical text (three sentences). UC-06 step 5
    quotes a shortened form omitting the final sentence ("Always consult a
    qualified legal professional before acting on any legal matter.").
    We use the Overview's full text as canonical, per instruction, and record
    the discrepancy in docs/assumptions.md (row A-01). This is NOT resolved.
"""

from __future__ import annotations

CANONICAL_DISCLAIMER: str = (
    "This response is provided for educational and training purposes only. "
    "It does not constitute legal advice. Always consult a qualified legal "
    "professional before acting on any legal matter."
)

#: The shortened variant quoted in UC-06 step 5. Held here ONLY so the boundary
#: check can recognise it as a near-miss and report a drift incident rather than
#: a generic mismatch. It is never emitted.
KNOWN_VARIANT_UC06_STEP5: str = (
    "This response is provided for educational and training purposes only. "
    "It does not constitute legal advice."
)

#: Name of the response field carrying the disclaimer, at every boundary.
DISCLAIMER_FIELD: str = "disclaimer"


def is_canonical(value: object) -> bool:
    """True only for the exact canonical string. No normalisation, no trimming.

    Deliberately strict: whitespace changes, casing changes, added prefixes and
    truncation are all failures. An 'almost right' disclaimer is a defect.
    """
    return isinstance(value, str) and value == CANONICAL_DISCLAIMER
