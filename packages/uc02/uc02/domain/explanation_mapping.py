"""NARIC level -> explanation profile mapping.

The scope document defines three templates. This module is the *only* place the
mapping lives: there are no ``if level == 3`` conditionals anywhere else in the
codebase. Changing the mapping means editing the two tables below.

    | NARIC level | Template     | Depth                    |
    |-------------|--------------|--------------------------|
    | 3           | basic        | A-level equivalent       |
    | 4           | basic        | (assumption A-03)        |
    | 5           | intermediate | Practitioner foundation  |
    | 6           | intermediate | (assumption A-03)        |
    | 7           | advanced     | Masters level            |
    | 7+ (8, 9)   | advanced     |                          |

Level 6 maps to ``intermediate``, never ``advanced``: a Level 6 qualification is
an undergraduate law degree, not a Masters, and over-mapping it pitches
explanations above the learner.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from uc02.domain.models.context import ExplanationProfile
from uc02.domain.models.enums import (
    AssumedPriorKnowledge,
    ExplanationDepth,
    ExplanationTemplateId,
    TerminologyLevel,
)

#: Explicit level -> template rows. Levels outside this table are clamped by
#: ``template_for_level`` using the boundaries below (assumption A-04).
LEVEL_TO_TEMPLATE: Mapping[int, ExplanationTemplateId] = MappingProxyType(
    {
        3: ExplanationTemplateId.BASIC,
        4: ExplanationTemplateId.BASIC,
        5: ExplanationTemplateId.INTERMEDIATE,
        6: ExplanationTemplateId.INTERMEDIATE,
        7: ExplanationTemplateId.ADVANCED,
        8: ExplanationTemplateId.ADVANCED,  # "Level 7+" (doctoral)
        9: ExplanationTemplateId.ADVANCED,  # "Level 7+"
    }
)

#: Lowest and highest levels the table covers; anything outside is clamped.
MIN_MAPPED_LEVEL = min(LEVEL_TO_TEMPLATE)
MAX_MAPPED_LEVEL = max(LEVEL_TO_TEMPLATE)

#: The three profiles, keyed by template id. Config, not code.
TEMPLATE_PROFILES: Mapping[ExplanationTemplateId, ExplanationProfile] = MappingProxyType(
    {
        ExplanationTemplateId.BASIC: ExplanationProfile(
            template_id=ExplanationTemplateId.BASIC,
            depth=ExplanationDepth.A_LEVEL_EQUIVALENT,
            terminology_level=TerminologyLevel.PLAIN_LANGUAGE,
            assumed_prior_knowledge=AssumedPriorKnowledge.MINIMAL,
            detail_level=1,
        ),
        ExplanationTemplateId.INTERMEDIATE: ExplanationProfile(
            template_id=ExplanationTemplateId.INTERMEDIATE,
            depth=ExplanationDepth.PRACTITIONER_FOUNDATION,
            terminology_level=TerminologyLevel.MIXED,
            assumed_prior_knowledge=AssumedPriorKnowledge.FOUNDATIONAL,
            detail_level=2,
        ),
        ExplanationTemplateId.ADVANCED: ExplanationProfile(
            template_id=ExplanationTemplateId.ADVANCED,
            depth=ExplanationDepth.MASTERS_LEVEL,
            terminology_level=TerminologyLevel.TECHNICAL,
            assumed_prior_knowledge=AssumedPriorKnowledge.SUBSTANTIAL,
            detail_level=3,
        ),
    }
)


def template_for_level(level: int) -> ExplanationTemplateId:
    """Return the template id for a NARIC level.

    Levels below the table clamp to the lowest row and levels above clamp to the
    highest, so an unexpected level never crashes assembly (assumption A-04).
    """
    if level in LEVEL_TO_TEMPLATE:
        return LEVEL_TO_TEMPLATE[level]
    clamped = min(max(level, MIN_MAPPED_LEVEL), MAX_MAPPED_LEVEL)
    return LEVEL_TO_TEMPLATE[clamped]


def profile_for_level(level: int) -> ExplanationProfile:
    """Return the full explanation profile for a NARIC level. Pure and deterministic."""
    return TEMPLATE_PROFILES[template_for_level(level)]
