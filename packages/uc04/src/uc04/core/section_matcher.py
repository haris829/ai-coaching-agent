"""Section identification: which part of the lesson is this question about?

Weighted lexical matching, no vector database and no embeddings. Each query token is weighted
by its inverse document frequency across the lesson's own sections, so a distinctive term
("hearsay", "compellability") counts far more than one the whole lesson uses ("evidence",
"court"). Tokens absent from the lesson keep a baseline weight, which is what pulls a genuinely
off-lesson question below the threshold rather than letting one incidental word carry it.

Two eligibility guards sit on top of the raw score:

* **anchor** - a query term must land on a section title or a concept name/keyword. Matching
  body prose alone is how an unrelated question latches onto a section that happens to reuse an
  everyday word.
* **substance** - one incidental token is not a topic. A single matched token only counts when
  it dominates a short question; otherwise at least two must match.

Thresholds live in ``uc04.core.thresholds`` and were re-tuned for this runtime, not copied.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import log

from ..domain.models import LessonConcept, LessonContent, LessonSection
from .text import content_tokens, unique_tokens
from .thresholds import (
    OOV_TOKEN_WEIGHT,
    SALIENT_FIELD_BOOST,
    SECTION_MATCH_THRESHOLD,
)


class MatchAnchor(str, Enum):
    """Strongest field of the section the question landed on."""

    NAME = "name"
    KEYWORD = "keyword"
    BODY = "body"
    NONE = "none"


@dataclass(frozen=True)
class SectionMatch:
    section: LessonSection
    concept: LessonConcept | None
    score: float
    anchor: MatchAnchor
    matched_tokens: int


@dataclass(frozen=True)
class MatchResult:
    #: Best match that cleared threshold and both guards, else None.
    best: SectionMatch | None
    #: Every scoring section, ranked. Diagnostics and the quiz topic fallback use this.
    ranked: tuple[SectionMatch, ...]


@dataclass(frozen=True)
class _Doc:
    section: LessonSection
    concepts: tuple[LessonConcept, ...]
    all_tokens: frozenset[str]
    salient_tokens: frozenset[str]
    name_tokens: frozenset[str]
    #: Full token set of each concept name and of the section title, kept whole so a query
    #: that names a concept outright can be recognised as such.
    full_name_sets: tuple[frozenset[str], ...]


@dataclass(frozen=True)
class _Scored:
    score: float
    matched_count: int
    anchor: MatchAnchor


class SectionMatcher:
    """Deterministic. Same question plus same lesson always yields the same match."""

    def __init__(self, threshold: float = SECTION_MATCH_THRESHOLD) -> None:
        self._threshold = threshold

    def match(self, question: str, lesson: LessonContent) -> MatchResult:
        query = unique_tokens(question)
        if not query or not lesson.sections:
            return MatchResult(best=None, ranked=())

        docs = self._build_docs(lesson)
        weights = self._token_weights(query, docs)
        total_weight = sum(weights.get(t, OOV_TOKEN_WEIGHT) for t in query)
        if total_weight <= 0:
            return MatchResult(best=None, ranked=())

        matches: list[SectionMatch] = []
        eligible: set[str] = set()
        for doc in docs:
            scored = self._score(query, weights, total_weight, doc.all_tokens, doc.salient_tokens, doc.name_tokens)
            if scored.score <= 0:
                continue
            named = self._names_a_concept(query, doc)
            if self._is_eligible(scored, len(query), named):
                eligible.add(doc.section.section_id)
            matches.append(
                SectionMatch(
                    section=doc.section,
                    concept=self._best_concept(query, weights, total_weight, doc),
                    score=round(scored.score, 4),
                    anchor=scored.anchor,
                    matched_tokens=scored.matched_count,
                )
            )

        matches.sort(key=lambda m: (-m.score, m.section.section_id))
        best = next(
            (m for m in matches if m.score >= self._threshold and m.section.section_id in eligible),
            None,
        )
        return MatchResult(best=best, ranked=tuple(matches))

    def find_concept(self, concept_tag: str, lesson: LessonContent) -> SectionMatch | None:
        concept = next((c for c in lesson.concepts if c.concept_tag == concept_tag), None)
        if concept is None:
            return None
        section = next((s for s in lesson.sections if s.section_id == concept.section_id), None)
        if section is None:
            return None
        return SectionMatch(section=section, concept=concept, score=1.0, anchor=MatchAnchor.NAME, matched_tokens=0)

    # ------------------------------------------------------------------------- internals

    def _build_docs(self, lesson: LessonContent) -> tuple[_Doc, ...]:
        docs: list[_Doc] = []
        for section in lesson.sections:
            concepts = tuple(c for c in lesson.concepts if c.section_id == section.section_id)
            name_tokens: set[str] = set(content_tokens(section.title))
            for concept in concepts:
                name_tokens.update(content_tokens(concept.name))
            salient = set(name_tokens)
            for concept in concepts:
                salient.update(content_tokens(" ".join(concept.keywords)))
            all_tokens = set(salient)
            all_tokens.update(content_tokens(section.body))
            all_tokens.update(content_tokens(" ".join(section.key_points)))
            for concept in concepts:
                all_tokens.update(content_tokens(concept.summary))
            full_names = [frozenset(content_tokens(c.name)) for c in concepts]
            full_names.append(frozenset(content_tokens(section.title)))
            docs.append(
                _Doc(
                    section=section,
                    concepts=concepts,
                    all_tokens=frozenset(all_tokens),
                    salient_tokens=frozenset(salient),
                    name_tokens=frozenset(name_tokens),
                    full_name_sets=tuple(f for f in full_names if f),
                )
            )
        return tuple(docs)

    def _token_weights(self, query: list[str], docs: tuple[_Doc, ...]) -> dict[str, float]:
        n = len(docs)
        weights: dict[str, float] = {}
        for token in query:
            df = sum(1 for doc in docs if token in doc.all_tokens)
            if df == 0:
                weights[token] = OOV_TOKEN_WEIGHT
                continue
            idf = log(1 + n / df) / log(1 + n)
            weights[token] = 0.35 + 1.65 * idf
        return weights

    def _score(
        self,
        query: list[str],
        weights: dict[str, float],
        total_weight: float,
        tokens: frozenset[str],
        salient: frozenset[str],
        names: frozenset[str] = frozenset(),
    ) -> _Scored:
        matched = 0.0
        matched_count = 0
        salient_hit = False
        name_hit = False
        for token in query:
            if token not in tokens:
                continue
            matched_count += 1
            weight = weights.get(token, OOV_TOKEN_WEIGHT)
            if token in names:
                name_hit = True
            if token in salient:
                salient_hit = True
                matched += weight * SALIENT_FIELD_BOOST
            else:
                matched += weight
        if matched_count == 0:
            anchor = MatchAnchor.NONE
        elif name_hit:
            anchor = MatchAnchor.NAME
        elif salient_hit:
            anchor = MatchAnchor.KEYWORD
        else:
            anchor = MatchAnchor.BODY
        return _Scored(score=min(1.0, matched / total_weight), matched_count=matched_count, anchor=anchor)

    def _names_a_concept(self, query: list[str], doc: _Doc) -> bool:
        """True when the question contains every token of a concept name or section title.

        "What does hearsay mean" names the concept outright; a single incidental hit on the
        word "standard" does not name "standard of proof". That distinction is what lets the
        substance guard stay strict without rejecting the most direct question a learner asks.
        """
        asked = set(query)
        return any(name_set <= asked for name_set in doc.full_name_sets)

    def _is_eligible(self, scored: _Scored, query_token_count: int, names_a_concept: bool) -> bool:
        if scored.anchor in (MatchAnchor.NONE, MatchAnchor.BODY):
            return False
        if names_a_concept:
            return True
        if scored.matched_count >= 2:
            return True
        return query_token_count > 0 and scored.matched_count / query_token_count >= 0.5

    def _best_concept(
        self, query: list[str], weights: dict[str, float], total_weight: float, doc: _Doc
    ) -> LessonConcept | None:
        best: LessonConcept | None = None
        best_score = 0.0
        for concept in doc.concepts:
            salient = frozenset(content_tokens(concept.name) + content_tokens(" ".join(concept.keywords)))
            all_tokens = frozenset(set(salient) | set(content_tokens(concept.summary)))
            scored = self._score(query, weights, total_weight, all_tokens, salient, salient)
            if scored.score > best_score:
                best_score = scored.score
                best = concept
        if best_score > 0:
            return best
        return doc.concepts[0] if doc.concepts else None
