"""A deterministic, template-driven explanation renderer.

Why this exists: the Definition of Done requires proof that a Level 3 learner and
a Level 7 learner receive *materially different* explanations. UC-02 assembles
context and must not call an LLM, so the difference is demonstrated with a pure
function over the resolved ``ExplanationProfile``.

This is not a coaching engine and it is not the platform's answer generator. It
renders a fixed scaffold at the register the profile dictates, so the mapping's
effect is observable and measurable in a unit test. Downstream use cases are
expected to consume ``ExplanationProfile`` fields directly.

Determinism: no randomness, no clock, no I/O. Same inputs -> byte-identical output.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from uc02.domain.models.context import ExplanationProfile
from uc02.domain.models.enums import ExplanationTemplateId

#: The defined term list used to measure terminology load. Multi-word terms are
#: matched as phrases. Kept small and explicit so the metric is auditable.
TECHNICAL_TERMS: frozenset[str] = frozenset(
    {
        "consideration",
        "promissory estoppel",
        "ratio decidendi",
        "obiter dicta",
        "vitiating factor",
        "statutory construction",
        "doctrinal",
        "precedent",
        "jurisprudence",
        "mens rea",
        "actus reus",
        "quantum",
        "detrimental reliance",
        "unjust enrichment",
        "equitable remedy",
        "burden of proof",
        "causation",
        "foreseeability",
        "remoteness",
        "privity",
    }
)

_TERM_PATTERNS: Mapping[str, re.Pattern[str]] = MappingProxyType(
    {term: re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in TECHNICAL_TERMS}
)


class RenderDirectives(BaseModel):
    """How a template renders. Configuration, one row per template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    analogy_led: bool
    gloss_technical_terms: bool
    body_sentences: tuple[str, ...]
    closing: str


#: Fragment banks per template. ``{question}`` is the only substitution.
RENDER_DIRECTIVES: Mapping[ExplanationTemplateId, RenderDirectives] = MappingProxyType(
    {
        ExplanationTemplateId.BASIC: RenderDirectives(
            analogy_led=True,
            gloss_technical_terms=True,
            body_sentences=(
                "Here is the short answer to: {question}",
                "Think of it like a promise you make when you buy a coffee: you hand over "
                "money, the shop hands over the drink, and both sides get something.",
                "The law looks for that same give-and-take before it will step in.",
            ),
            closing=(
                "In plain terms: each side must give something for the deal to count."
            ),
        ),
        ExplanationTemplateId.INTERMEDIATE: RenderDirectives(
            analogy_led=False,
            gloss_technical_terms=True,
            body_sentences=(
                "Answering: {question}",
                "The starting point is consideration, meaning the value each party gives in "
                "exchange for the other's promise.",
                "Where one party has relied on a promise to their cost, promissory estoppel "
                "may operate, though it is a shield rather than a sword.",
                "Applying precedent to the facts is what determines the practical outcome "
                "here.",
            ),
            closing=(
                "For practice: identify the exchange first, then test whether reliance "
                "changes the analysis."
            ),
        ),
        ExplanationTemplateId.ADVANCED: RenderDirectives(
            analogy_led=False,
            gloss_technical_terms=False,
            body_sentences=(
                "Question under analysis: {question}",
                "The doctrinal starting point is consideration, and the authorities treat "
                "its sufficiency rather than its adequacy as the operative question.",
                "Promissory estoppel intervenes only on detrimental reliance, and the ratio "
                "decidendi of the leading authorities confines it to a defensive posture.",
                "The relevant jurisprudence distinguishes the ratio decidendi from obiter "
                "dicta when assessing how far precedent binds on these facts.",
                "Where a vitiating factor is pleaded, statutory construction and the burden "
                "of proof allocation determine whether the claim survives.",
                "Quantum is then assessed against causation, remoteness and foreseeability, "
                "with unjust enrichment supplying an alternative equitable remedy.",
            ),
            closing=(
                "The analysis therefore turns on whether privity and the pleaded vitiating factor "
                "displace the primary doctrinal route."
            ),
        ),
    }
)


class ExplanationMetrics(BaseModel):
    """Measurable properties of rendered output. Used by tests, not by callers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    word_count: int
    sentence_count: int
    avg_sentence_length: float
    technical_term_occurrences: int
    distinct_technical_terms: int


class RenderedExplanation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: ExplanationTemplateId
    text: str
    metrics: ExplanationMetrics


def count_technical_terms(text: str) -> tuple[int, int]:
    """Return (total occurrences, distinct terms) from ``TECHNICAL_TERMS``."""
    occurrences = 0
    distinct = 0
    for pattern in _TERM_PATTERNS.values():
        found = len(pattern.findall(text))
        if found:
            occurrences += found
            distinct += 1
    return occurrences, distinct


def measure(text: str) -> ExplanationMetrics:
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    occurrences, distinct = count_technical_terms(text)
    return ExplanationMetrics(
        word_count=len(words),
        sentence_count=len(sentences),
        avg_sentence_length=round(len(words) / len(sentences), 2) if sentences else 0.0,
        technical_term_occurrences=occurrences,
        distinct_technical_terms=distinct,
    )


def render_explanation(question: str, profile: ExplanationProfile) -> RenderedExplanation:
    """Render ``question`` at the register described by ``profile``. Pure function."""
    directives = RENDER_DIRECTIVES[profile.template_id]
    body = [sentence.format(question=question.strip()) for sentence in directives.body_sentences]
    body.append(directives.closing)
    text = " ".join(body)
    return RenderedExplanation(template_id=profile.template_id, text=text, metrics=measure(text))
