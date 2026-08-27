"""Mock ConceptTagger: deterministic closed-vocabulary matching.

Longest surface form wins, so "hearsay exception" beats "hearsay". Nothing matched becomes
``unclassified`` - never a free-form tag invented from the question.
"""

from __future__ import annotations

from ...domain.enums import UNCLASSIFIED
from ...domain.errors import ProviderUnavailable
from ...domain.models import ConceptTag, LessonContent
from ...domain.vocabularies import CONCEPT_VOCABULARY, topic_for_concept
from ...core.text import content_tokens, contains_all

PORT = "concept_tagger"

#: Any question containing this marker drives the unavailable scenario.
UNAVAILABLE_MARKER = "__tagger_down__"


class MockConceptTagger:
    name = "mock"

    def tag(self, question: str, lesson: LessonContent | None) -> ConceptTag:
        if UNAVAILABLE_MARKER in question:
            raise ProviderUnavailable(PORT, "tagger unavailable")

        asked = content_tokens(question)
        if not asked:
            return ConceptTag(concept_tag=UNCLASSIFIED, topic_tag=UNCLASSIFIED, matched=False)

        best_tag: str | None = None
        best_specificity = -1
        for entry in CONCEPT_VOCABULARY:
            for surface in entry.surface_forms:
                needles = content_tokens(surface)
                if not needles or not contains_all(asked, needles):
                    continue
                # Prefer the most specific surface form that matched.
                if len(needles) > best_specificity:
                    best_specificity = len(needles)
                    best_tag = entry.concept_tag

        if best_tag is None:
            return ConceptTag(concept_tag=UNCLASSIFIED, topic_tag=UNCLASSIFIED, matched=False)
        return ConceptTag(concept_tag=best_tag, topic_tag=topic_for_concept(best_tag), matched=True)
