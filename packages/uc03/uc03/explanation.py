"""Deterministic explanation-depth profiles and framing selection.

Requirement 2 says the plain-English explanation must adapt to the learner's
NARIC level, and explicitly *not* by changing wording randomly. So the mapping
NARIC level -> depth -> profile is a pure function, and the profile carries
concrete, assertable knobs (sentence budget, whether to scaffold with an
analogy, whether technical vocabulary is allowed unglossed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .domain.enums import (
    ALL_FRAMINGS,
    DEFAULT_NARIC_LEVEL,
    ExplanationDepth,
    FramingStrategy,
    NaricLevel,
)

_NARIC_TO_DEPTH: dict[NaricLevel, ExplanationDepth] = {
    NaricLevel.LEVEL_3: ExplanationDepth.FOUNDATION,
    NaricLevel.LEVEL_4: ExplanationDepth.FOUNDATION,
    NaricLevel.LEVEL_5: ExplanationDepth.INTERMEDIATE,
    NaricLevel.LEVEL_6: ExplanationDepth.INTERMEDIATE,
    NaricLevel.LEVEL_7: ExplanationDepth.ADVANCED,
    NaricLevel.LEVEL_7_PLUS: ExplanationDepth.ADVANCED,
}

#: Ordered shallowest to deepest, for GO_DEEPER.
DEPTH_LADDER: tuple[ExplanationDepth, ...] = (
    ExplanationDepth.FOUNDATION,
    ExplanationDepth.INTERMEDIATE,
    ExplanationDepth.ADVANCED,
)


def normalise_level(level: object) -> NaricLevel:
    """Coerce anything that is not a recognised qualification level to the default.

    A context adapter that yields a level outside the closed set is a bug in
    that adapter, but it must degrade rather than crash the service.
    """
    if isinstance(level, NaricLevel):
        return level
    try:
        return NaricLevel(level)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return DEFAULT_NARIC_LEVEL


def depth_for(level: object) -> ExplanationDepth:
    """Total function: every input yields a depth, none raise."""
    return _NARIC_TO_DEPTH[normalise_level(level)]


def deepen(depth: ExplanationDepth) -> ExplanationDepth:
    """One step down the ladder; already-deepest stays put."""
    index = DEPTH_LADDER.index(depth)
    return DEPTH_LADDER[min(index + 1, len(DEPTH_LADDER) - 1)]


@dataclass(frozen=True)
class ExplanationProfile:
    depth: ExplanationDepth
    max_sentences: int
    use_analogy: bool
    gloss_technical_terms: bool
    register: str
    instruction: str


_PROFILES: dict[ExplanationDepth, ExplanationProfile] = {
    ExplanationDepth.FOUNDATION: ExplanationProfile(
        depth=ExplanationDepth.FOUNDATION,
        max_sentences=4,
        use_analogy=True,
        gloss_technical_terms=True,
        register="everyday",
        instruction=(
            "Explain in everyday language with no assumed legal background. "
            "Gloss every technical term in parentheses the first time it "
            "appears. At most 4 sentences."
        ),
    ),
    ExplanationDepth.INTERMEDIATE: ExplanationProfile(
        depth=ExplanationDepth.INTERMEDIATE,
        max_sentences=5,
        use_analogy=False,
        gloss_technical_terms=True,
        register="undergraduate",
        instruction=(
            "Explain for a law undergraduate. Assume basic legal vocabulary but "
            "gloss specialist terms. Prefer precision over analogy. At most 5 "
            "sentences."
        ),
    ),
    ExplanationDepth.ADVANCED: ExplanationProfile(
        depth=ExplanationDepth.ADVANCED,
        max_sentences=3,
        use_analogy=False,
        gloss_technical_terms=False,
        register="practitioner",
        instruction=(
            "Explain for a postgraduate or practitioner. Use technical "
            "vocabulary directly without glossing. Be concise and doctrinally "
            "precise. At most 3 sentences."
        ),
    ),
}


def profile_for(level: object) -> ExplanationProfile:
    return _PROFILES[depth_for(level)]


def profile_for_depth(depth: ExplanationDepth) -> ExplanationProfile:
    return _PROFILES[depth]


# --------------------------------------------------------------------------
# Framing selection
# --------------------------------------------------------------------------

#: Preference order per follow-up action. Every action still draws from the same
#: pool of framings and may never reuse one within a session for a concept.
_ACTION_PREFERENCE: dict[str, tuple[FramingStrategy, ...]] = {
    "another_example": (
        FramingStrategy.WORKED_EXAMPLE,
        FramingStrategy.ANALOGY,
        FramingStrategy.CONTRAST_NEAR_MISS,
        FramingStrategy.PROCEDURAL_WALKTHROUGH,
        FramingStrategy.MISCONCEPTION_CORRECTION,
        FramingStrategy.FIRST_PRINCIPLES,
    ),
    "go_deeper": (
        FramingStrategy.FIRST_PRINCIPLES,
        FramingStrategy.CONTRAST_NEAR_MISS,
        FramingStrategy.MISCONCEPTION_CORRECTION,
        FramingStrategy.PROCEDURAL_WALKTHROUGH,
        FramingStrategy.WORKED_EXAMPLE,
        FramingStrategy.ANALOGY,
    ),
    "explain_differently": (
        FramingStrategy.ANALOGY,
        FramingStrategy.CONTRAST_NEAR_MISS,
        FramingStrategy.MISCONCEPTION_CORRECTION,
        FramingStrategy.WORKED_EXAMPLE,
        FramingStrategy.PROCEDURAL_WALKTHROUGH,
        FramingStrategy.FIRST_PRINCIPLES,
    ),
}

#: Framing used for a learner's first question on a concept, and the fallback
#: when no framing registry is configured. Matches the first entry of
#: ALL_FRAMINGS, which is what `select_framing(action=None, used=())` returns.
INITIAL_FRAMING = FramingStrategy.ANALOGY


def select_framing(
    *, action: str | None, used: frozenset[FramingStrategy]
) -> FramingStrategy | None:
    """Pick an unused framing, honouring the action's preference order.

    Returns None when every framing has been used - the caller must then say so
    rather than cycling back to the first.
    """
    order = _ACTION_PREFERENCE.get(action or "", ALL_FRAMINGS)
    for framing in order:
        if framing not in used:
            return framing
    for framing in ALL_FRAMINGS:  # preference list exhausted, try the rest
        if framing not in used:
            return framing
    return None


def concept_key(topic_tag: str, subject: str) -> str:
    """Stable identity for "the concept being asked about".

    Deliberately coarse: the same concept asked three different ways must land
    on the same key, or the never-repeat-a-framing rule would not bind.
    """
    normalised = re.sub(r"[^a-z0-9 ]+", "", subject.lower())
    tokens = sorted({t for t in normalised.split() if len(t) > 3})
    return f"{topic_tag}|{'-'.join(tokens)}" if tokens else topic_tag
