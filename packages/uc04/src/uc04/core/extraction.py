"""Extraction budget - the ceiling on how much lesson text can be recovered by asking.

Lesson content is company intellectual property. An explanation is a *transformation* of source
material, not a projection of it, and "explain differently" must not become an enumeration
primitive that walks a learner through the whole lesson.

Three rules, all deterministic and all testable:

1. **Section body prose is never quotable.** Only curated material - a concept's definition and
   a section's key points - may be quoted at all. There is no fallthrough to
   ``sentences(section.body)``; where there is nothing curated to transform, UC-04 says the
   lesson does not cover the point in enough depth to explain differently.
2. **A fixed span budget per concept.** At most ``MAX_QUOTED_SPANS_PER_CONCEPT`` distinct source
   spans are ever quotable for one concept, chosen deterministically from the front of the
   candidate list. Every framing draws from that same small set, so more requests do not buy
   more material.
3. **A per-response and per-span cap.** At most ``MAX_QUOTED_SPANS_PER_RESPONSE`` spans appear
   in any one response, each truncated to ``MAX_QUOTED_SPAN_WORDS`` words.

The residual - a defined term's definition is largely unavoidable when explaining that term -
is recorded in docs/assumptions.md (A-10) rather than left implied.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import LessonConcept, LessonSection
from .text import truncate_words
from .thresholds import (
    MAX_QUOTED_SPAN_WORDS,
    MAX_QUOTED_SPANS_PER_CONCEPT,
    MAX_QUOTED_SPANS_PER_RESPONSE,
)


@dataclass(frozen=True)
class QuotableMaterial:
    """The only lesson text a generator is permitted to reproduce for this concept."""

    spans: tuple[str, ...]
    #: True when the lesson supplies nothing curated to work from.
    empty: bool

    @property
    def exhausted(self) -> bool:
        return self.empty


def quotable_material(
    section: LessonSection | None,
    concept: LessonConcept | None,
) -> QuotableMaterial:
    """Deterministically select the spans inside budget.

    Order matters and is fixed: the concept definition first (a learner asking about a term
    needs it), then the section's key points in their authored order. The budget is applied to
    the front of that list, so the same three spans are offered on every request - a fourth
    request cannot reach a fourth span.
    """
    candidates: list[str] = []
    if concept is not None and concept.summary.strip():
        candidates.append(concept.summary.strip())
    if section is not None:
        candidates.extend(point.strip() for point in section.key_points if point.strip())

    # NOTE: section.body is deliberately absent. See rule 1 above.
    budgeted = candidates[:MAX_QUOTED_SPANS_PER_CONCEPT]
    spans = tuple(truncate_words(span, MAX_QUOTED_SPAN_WORDS) for span in budgeted)
    return QuotableMaterial(spans=spans, empty=not spans)


def spans_for_response(material: QuotableMaterial, framing_index: int) -> tuple[str, ...]:
    """Pick this response's slice of the budgeted spans.

    Rotating by framing index varies which of the (already bounded) spans a given framing
    leans on, without ever widening the set.
    """
    if not material.spans:
        return ()
    count = min(MAX_QUOTED_SPANS_PER_RESPONSE, len(material.spans))
    start = framing_index % len(material.spans)
    rotated = material.spans[start:] + material.spans[:start]
    return rotated[:count]
