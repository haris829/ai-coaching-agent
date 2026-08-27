"""Grounding: the rule that every claim in the document is true of the session.

This module is the reason the component can be handed to a regulator. A CPD
evidence document carries a learner name; a section listing a topic that was
not discussed or an authority that was not cited is a false record with that
name on it. So every element of every section is checked against the recorded
session data before it can reach a stored summary, and a response that fails
the check is **rejected whole**.

Rejected whole, not stripped. Stripping the bad element and keeping the rest
would turn a visible failure into an invisible one, and a generator that
fabricates once will fabricate again. The failure must be loud.

What grounds what
-----------------

===================== ==========================================================
Section               Admissible source
===================== ==========================================================
Topics Covered        ``InteractionRecord.topic_tags`` within the cover window
Key Concepts          ``InteractionRecord.concept_tags``, tied to a grounded
                      topic and to real interaction ids within the window
Resources Referenced  ``CitationProvider`` records for this session, matched on
                      ``resource_id`` *and* on the citation string
Next Steps            gap-report suggestion ids, or a grounded session topic
===================== ==========================================================

Nothing else is admissible. Relevance to the topic is not a source. Usefulness
to the learner is not a source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from uc09_summary.domain.enums import SuggestionSource
from uc09_summary.domain.errors import GroundingViolation
from uc09_summary.domain.models import (
    InteractionRecord,
    Resource,
    SessionRecord,
    Suggestion,
    SummaryContent,
)

#: Bounds fixed by the specification. Upper bounds are enforced as contract
#: violations; falling short of a lower bound is reported as a ``partial``
#: section, because padding a section to hit a number is forbidden.
MAX_KEY_CONCEPTS = 5
MIN_KEY_CONCEPTS = 3
MAX_NEXT_STEPS = 3
MIN_NEXT_STEPS = 2


@dataclass(frozen=True)
class SessionData:
    """Everything the generator may draw on, and the only thing grounding trusts.

    Attributes:
        session: the session record.
        interactions: interactions at or before ``covers_interactions_through``.
        citations: authorities actually cited during the session.
        gap_suggestions: gap-report suggestions, or ``None`` when the gap report
            was unavailable. ``None`` and ``[]`` are different states.
        covers_interactions_through: the cover window end. Interactions after
            this instant are not part of this summary and cannot ground anything.
    """

    session: SessionRecord
    interactions: tuple[InteractionRecord, ...]
    citations: tuple[Resource, ...]
    gap_suggestions: tuple[Suggestion, ...] | None
    covers_interactions_through: datetime

    @property
    def is_single_topic(self) -> bool:
        return len(self.topic_ids) == 1

    @property
    def topic_ids(self) -> tuple[str, ...]:
        """Distinct topic tags in first-occurrence order. The whole admissible set."""
        seen: dict[str, None] = {}
        for interaction in self.interactions:
            for tag in interaction.topic_tags:
                seen.setdefault(tag, None)
        return tuple(seen)

    @property
    def concept_ids(self) -> tuple[str, ...]:
        """Distinct concept tags in first-occurrence order."""
        seen: dict[str, None] = {}
        for interaction in self.interactions:
            for tag in interaction.concept_tags:
                seen.setdefault(tag, None)
        return tuple(seen)

    @property
    def interaction_ids(self) -> frozenset[str]:
        return frozenset(i.interaction_id for i in self.interactions)

    def interactions_for_topic(self, topic_id: str) -> tuple[InteractionRecord, ...]:
        return tuple(i for i in self.interactions if topic_id in i.topic_tags)

    def interactions_for_concept(self, concept_id: str) -> tuple[InteractionRecord, ...]:
        return tuple(i for i in self.interactions if concept_id in i.concept_tags)


@dataclass
class _Violations:
    entries: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, kind: str, identifier: str, reason: str) -> None:
        # Identifiers and machine reasons only. Never the prose of the claim -
        # a rejected authority title or concept explanation must not travel
        # with the error, because the error is written to logs.
        self.entries.append((kind, identifier, reason))

    @property
    def items(self) -> list[str]:
        """Full messages, for the operator-facing exception detail."""
        return [f"{kind}[{ident}]: {reason}" for kind, ident, reason in self.entries]

    @property
    def reasons(self) -> list[str]:
        """Kind and machine reason only, with the identifier dropped.

        This is what gets logged. A concept or topic identifier says which
        subject a named learner was studying, so it stays out of application
        logs even inside an error record.
        """
        return [f"{kind}: {reason}" for kind, _ident, reason in self.entries]


def check_grounding(content: SummaryContent, data: SessionData) -> None:
    """Validate every element of every section against the session record.

    Args:
        content: what the generator returned.
        data: the recorded session data, and the only admissible source.

    Raises:
        GroundingViolation: if any element cannot be traced to session data, or
            if a section breaches a specification bound. The exception carries
            every violation found, not just the first, so that one rejection
            reports the full extent of the problem.
    """
    v = _Violations()

    admissible_topics = set(data.topic_ids)
    admissible_concepts = set(data.concept_ids)
    admissible_interactions = data.interaction_ids
    citations_by_id = {r.resource_id: r for r in data.citations}
    gap_ids = {s.suggestion_id for s in (data.gap_suggestions or ())}

    _check_topics(content, admissible_topics, data, v)
    grounded_topic_ids = {t.topic_id for t in content.topics_covered} & admissible_topics
    _check_concepts(content, admissible_concepts, admissible_interactions, grounded_topic_ids, v)
    _check_resources(content, citations_by_id, v)
    _check_next_steps(content, gap_ids, grounded_topic_ids, data, v)

    if v.entries:
        raise GroundingViolation(v.items, reasons=v.reasons)


def _check_topics(
    content: SummaryContent,
    admissible: set[str],
    data: SessionData,
    v: _Violations,
) -> None:
    seen: set[str] = set()
    for topic in content.topics_covered:
        if topic.topic_id not in admissible:
            # The core rule: no inference, no expansion, no "related to".
            v.add("topic", topic.topic_id, "not present in session topic tags")
            continue
        if topic.topic_id in seen:
            v.add("topic", topic.topic_id, "duplicated in topics_covered")
        seen.add(topic.topic_id)

        supporting = data.interactions_for_topic(topic.topic_id)
        if topic.interaction_count != len(supporting):
            v.add(
                "topic",
                topic.topic_id,
                f"interaction_count {topic.interaction_count} does not match "
                f"{len(supporting)} tagged interactions",
            )
        if supporting:
            first = min(i.occurred_at for i in supporting)
            last = max(i.occurred_at for i in supporting)
            if topic.first_discussed_at != first or topic.last_discussed_at != last:
                v.add("topic", topic.topic_id, "discussion window does not match tag record")

    # A session that has tags but a summary that lists no topic is also wrong:
    # the section would understate the session rather than overstate it, but a
    # record of professional development should not do either.
    if admissible and not content.topics_covered:
        v.add("topics_covered", "*", "session has topic tags but no topic was reported")


def _check_concepts(
    content: SummaryContent,
    admissible_concepts: set[str],
    admissible_interactions: frozenset[str],
    grounded_topic_ids: set[str],
    v: _Violations,
) -> None:
    if len(content.key_concepts) > MAX_KEY_CONCEPTS:
        v.add(
            "key_concepts",
            "*",
            f"{len(content.key_concepts)} concepts exceeds the maximum of {MAX_KEY_CONCEPTS}",
        )

    seen: set[str] = set()
    for concept in content.key_concepts:
        if concept.concept_id not in admissible_concepts:
            v.add("concept", concept.concept_id, "not present in session concept tags")
        if concept.concept_id in seen:
            v.add("concept", concept.concept_id, "duplicated in key_concepts")
        seen.add(concept.concept_id)

        if concept.topic_id not in grounded_topic_ids:
            v.add(
                "concept",
                concept.concept_id,
                "attached to a topic that is not grounded in this session",
            )

        unknown = [
            i for i in concept.evidence_interaction_ids if i not in admissible_interactions
        ]
        if unknown:
            v.add(
                "concept",
                concept.concept_id,
                f"cites {len(unknown)} interaction id(s) not in the session record",
            )


def _check_resources(
    content: SummaryContent,
    citations_by_id: dict[str, Resource],
    v: _Violations,
) -> None:
    seen: set[str] = set()
    for resource in content.resources_referenced:
        recorded = citations_by_id.get(resource.resource_id)
        if recorded is None:
            # The rule that matters most on a document of record: an authority
            # that was not cited in this session cannot appear, however relevant.
            v.add("resource", resource.resource_id, "not cited during this session")
            continue
        if resource.resource_id in seen:
            v.add("resource", resource.resource_id, "duplicated in resources_referenced")
        seen.add(resource.resource_id)

        if _normalise_citation(resource.citation) != _normalise_citation(recorded.citation):
            v.add("resource", resource.resource_id, "citation text differs from the session record")
        if resource.title != recorded.title:
            v.add("resource", resource.resource_id, "title differs from the session record")
        if resource.kind != recorded.kind:
            v.add("resource", resource.resource_id, "kind differs from the session record")


def _check_next_steps(
    content: SummaryContent,
    gap_ids: set[str],
    grounded_topic_ids: set[str],
    data: SessionData,
    v: _Violations,
) -> None:
    if len(content.next_steps) > MAX_NEXT_STEPS:
        v.add(
            "next_steps",
            "*",
            f"{len(content.next_steps)} suggestions exceeds the maximum of {MAX_NEXT_STEPS}",
        )

    seen: set[str] = set()
    for suggestion in content.next_steps:
        if suggestion.suggestion_id in seen:
            v.add("suggestion", suggestion.suggestion_id, "duplicated in next_steps")
        seen.add(suggestion.suggestion_id)

        if suggestion.source is SuggestionSource.GAP_REPORT:
            if data.gap_suggestions is None:
                v.add(
                    "suggestion",
                    suggestion.suggestion_id,
                    "claims gap_report provenance but the gap report was unavailable",
                )
            elif suggestion.suggestion_id not in gap_ids:
                v.add(
                    "suggestion",
                    suggestion.suggestion_id,
                    "not present in the gap report response",
                )
        elif suggestion.source is SuggestionSource.SESSION_CONTENT:
            if suggestion.related_topic_id is None:
                v.add(
                    "suggestion",
                    suggestion.suggestion_id,
                    "session_content provenance requires a related_topic_id",
                )
            elif suggestion.related_topic_id not in grounded_topic_ids:
                v.add(
                    "suggestion",
                    suggestion.suggestion_id,
                    "related_topic_id is not a topic discussed in this session",
                )
        else:  # pragma: no cover - the enum is closed
            v.add("suggestion", suggestion.suggestion_id, "unknown provenance")


def _normalise_citation(citation: str) -> str:
    """Compare citations ignoring incidental whitespace and case only."""
    return " ".join(citation.split()).casefold()
