"""Deterministic ``SummaryGenerator`` and the misbehaving variants the tests need.

:class:`DeterministicSummaryGenerator` is the default and the whole test suite
runs against it. No network, no API key, no model. It derives the four sections
from the session record by transformation only, so it is grounded by
construction - and it is *still* put through the same grounding check as any
other generator, because a check that only runs for suspect implementations is
a check nobody trusts.

Two rules it honours that a plausible implementation would get wrong:

* It never pads. A single-topic session yields one topic; the depth goes into
  the concepts, not into an inflated topic list. Where fewer than three
  concepts can be grounded, it returns fewer and the section is reported
  ``partial``.
* It never reaches for an authority that was not cited, however relevant that
  authority would be to the topic.

The other classes in this module are deliberately faulty generators used to
prove the rejection paths. They exist so that the failure behaviour is tested
against something that really misbehaves.
"""

from __future__ import annotations

from datetime import UTC

from uc09_summary.domain.enums import (
    ExplanationProfile,
    ResourceKind,
    SuggestionSource,
)
from uc09_summary.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc09_summary.domain.grounding import (
    MAX_KEY_CONCEPTS,
    MAX_NEXT_STEPS,
    MIN_NEXT_STEPS,
    SessionData,
)
from uc09_summary.domain.models import (
    Concept,
    InteractionRecord,
    Resource,
    Suggestion,
    SummaryContent,
    Topic,
)
from uc09_summary.domain.naric import explanation_profile_for

PORT = "summary_generator"


class DeterministicSummaryGenerator:
    """Builds the four sections from session records, with no external call."""

    @classmethod
    def from_settings(cls, settings: object) -> DeterministicSummaryGenerator:
        return cls()

    def generate(self, session_data: SessionData) -> SummaryContent:
        """Derive the four sections from ``session_data``."""
        profile = explanation_profile_for(session_data.session.naric_level)
        topics = _build_topics(session_data)
        concepts = _build_concepts(session_data, topics, profile)
        resources = _build_resources(session_data)
        steps, notes = _build_next_steps(session_data, topics)
        notes.update(_section_notes(session_data, topics, concepts, resources))
        return SummaryContent(
            topics_covered=topics,
            key_concepts=concepts,
            resources_referenced=resources,
            next_steps=steps,
            section_notes=notes,
        )

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        return {"upstream_tokens": ("DeterministicSummaryGenerator",)}


# --------------------------------------------------------------------------
# Section construction
# --------------------------------------------------------------------------


def _build_topics(data: SessionData) -> tuple[Topic, ...]:
    """One topic per distinct tag. No inference, no expansion, no padding."""
    topics: list[Topic] = []
    for topic_id in data.topic_ids:
        supporting = data.interactions_for_topic(topic_id)
        topics.append(
            Topic(
                topic_id=topic_id,
                label=_humanise(topic_id),
                interaction_count=len(supporting),
                first_discussed_at=min(i.occurred_at for i in supporting),
                last_discussed_at=max(i.occurred_at for i in supporting),
            )
        )
    return tuple(topics)


def _build_concepts(
    data: SessionData,
    topics: tuple[Topic, ...],
    profile: ExplanationProfile,
) -> tuple[Concept, ...]:
    """Select up to five concept tags and explain each at the learner profile depth.

    A single-topic session gets the deeper explanation and the full allowance of
    concepts. That is the specified behaviour: depth instead of breadth.
    """
    topic_ids = {t.topic_id for t in topics}
    candidates: list[tuple[int, int, str]] = []
    for order, concept_id in enumerate(data.concept_ids):
        supporting = data.interactions_for_concept(concept_id)
        if not supporting:
            continue
        if not any(tag in topic_ids for i in supporting for tag in i.topic_tags):
            # A concept whose interactions carry no grounded topic cannot be
            # attached to one, so it is dropped rather than guessed at.
            continue
        candidates.append((-len(supporting), order, concept_id))

    candidates.sort()
    chosen = candidates[:MAX_KEY_CONCEPTS]
    # Restore first-occurrence order so the document reads chronologically.
    chosen.sort(key=lambda item: item[1])

    concepts: list[Concept] = []
    for _, _, concept_id in chosen:
        supporting = data.interactions_for_concept(concept_id)
        topic_id = _owning_topic(supporting, topic_ids)
        concepts.append(
            Concept(
                concept_id=concept_id,
                label=_humanise(concept_id),
                explanation=_explain(
                    concept_id, supporting, topic_id, data, profile
                ),
                topic_id=topic_id,
                evidence_interaction_ids=tuple(i.interaction_id for i in supporting),
            )
        )
    return tuple(concepts)


def _build_resources(data: SessionData) -> tuple[Resource, ...]:
    """Pass through the citation record, unchanged and unextended.

    There is deliberately no enrichment step here. Adding the authority a
    reader "would expect" for a topic is exactly how a CPD record becomes false.
    """
    return tuple(
        sorted(
            data.citations,
            key=lambda r: (r.first_cited_at is None, r.first_cited_at, r.resource_id),
        )
    )


def _build_next_steps(
    data: SessionData, topics: tuple[Topic, ...]
) -> tuple[tuple[Suggestion, ...], dict[str, str]]:
    """Take gap-report suggestions first, then top up from session content.

    When the gap report is unavailable, Next Steps degrades to session-derived
    suggestions; when neither source yields anything, the section is omitted
    with a note. Nothing is invented in any branch.
    """
    notes: dict[str, str] = {}
    chosen: list[Suggestion] = []

    gap = data.gap_suggestions
    if gap:
        chosen.extend(gap[:MAX_NEXT_STEPS])
    elif gap is None:
        notes["next_steps"] = (
            "The gap report was unavailable for this learner, so these "
            "suggestions are drawn from this session only."
        )
    else:
        notes["next_steps"] = (
            "The gap report returned no suggestions, so these suggestions are "
            "drawn from this session only."
        )

    if len(chosen) < MIN_NEXT_STEPS:
        for suggestion in _session_derived(topics):
            if len(chosen) >= MIN_NEXT_STEPS:
                break
            chosen.append(suggestion)

    if not chosen:
        notes["next_steps"] = (
            "No next step could be grounded in this session or in a gap report, "
            "so none is offered. Nothing has been inferred."
        )

    return tuple(chosen[:MAX_NEXT_STEPS]), notes


def _session_derived(topics: tuple[Topic, ...]) -> list[Suggestion]:
    """Suggestions grounded in topics actually discussed, least-covered first."""
    ordered = sorted(topics, key=lambda t: (t.interaction_count, t.first_discussed_at))
    return [
        Suggestion(
            suggestion_id=f"session-{topic.topic_id}",
            label=f"Continue with {_humanise(topic.topic_id).lower()}",
            rationale=(
                f"Raised in {topic.interaction_count} interaction"
                f"{'' if topic.interaction_count == 1 else 's'} in this session."
            ),
            source=SuggestionSource.SESSION_CONTENT,
            related_topic_id=topic.topic_id,
        )
        for topic in ordered
    ]


def _section_notes(
    data: SessionData,
    topics: tuple[Topic, ...],
    concepts: tuple[Concept, ...],
    resources: tuple[Resource, ...],
) -> dict[str, str]:
    notes: dict[str, str] = {}
    if data.is_single_topic and topics:
        notes["topics_covered"] = (
            "This session covered a single topic. The Key Concepts section "
            "goes into greater depth rather than listing further topics."
        )
    if 0 < len(concepts) < 3:
        notes["key_concepts"] = (
            f"Only {len(concepts)} key concept"
            f"{'' if len(concepts) == 1 else 's'} could be drawn from the "
            "recorded interactions. The section has not been padded."
        )
    if not resources:
        notes["resources_referenced"] = (
            "No legislation or case law was cited during this session, so "
            "nothing is listed. Authorities relevant to the topic are "
            "deliberately not added here."
        )
    return notes


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------


def _explain(
    concept_id: str,
    supporting: tuple[InteractionRecord, ...],
    topic_id: str,
    data: SessionData,
    profile: ExplanationProfile,
) -> str:
    """Compose an explanation from recorded facts only, at the profile depth."""
    count = len(supporting)
    plural = "" if count == 1 else "s"
    topic_label = _humanise(topic_id).lower()
    first = min(i.occurred_at for i in supporting)

    if profile is ExplanationProfile.BASIC:
        text = (
            f"You looked at {_humanise(concept_id).lower()} while working on "
            f"{topic_label}, across {count} question{plural} in this session."
        )
    elif profile is ExplanationProfile.INTERMEDIATE:
        text = (
            f"You explored {_humanise(concept_id).lower()} as part of "
            f"{topic_label}, across {count} question{plural}, first raised at "
            f"{_clock(first)}."
        )
    else:
        text = (
            f"You examined {_humanise(concept_id).lower()} within "
            f"{topic_label}, across {count} question{plural} from "
            f"{_clock(first)}, at the level of detail expected at "
            f"{data.session.naric_level.value}."
        )

    linked = _authorities_for(supporting, data)
    if linked:
        text += " Authority cited alongside it: " + "; ".join(linked) + "."

    if data.is_single_topic:
        # Depth, not breadth: the single-topic case adds detail here instead of
        # adding a topic that was never discussed.
        position = data.interactions.index(supporting[0]) + 1
        text += (
            f" This was interaction {position} of {len(data.interactions)} in a "
            "session devoted to this one topic."
        )
    return text


def _authorities_for(
    supporting: tuple[InteractionRecord, ...], data: SessionData
) -> list[str]:
    """Authorities cited *in these very interactions*. Never a relevance lookup."""
    ids = {i.interaction_id for i in supporting}
    return [
        resource.citation
        for resource in data.citations
        if ids & set(resource.cited_in_interaction_ids)
    ]


def _owning_topic(
    supporting: tuple[InteractionRecord, ...], topic_ids: set[str]
) -> str:
    for interaction in supporting:
        for tag in interaction.topic_tags:
            if tag in topic_ids:
                return tag
    raise ProviderInvalidResponse(  # pragma: no cover - guarded by the caller
        PORT, "concept has no grounded topic"
    )


def _humanise(tag: str) -> str:
    words = tag.replace("_", "-").split("-")
    return " ".join(words).capitalize() if words else tag


def _clock(value: object) -> str:
    dt = value  # datetime, kept loose to avoid an import cycle in typing
    return dt.astimezone(UTC).strftime("%H:%M UTC")  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Deliberately faulty generators, for the rejection tests
# --------------------------------------------------------------------------


class UngroundedTopicGenerator(DeterministicSummaryGenerator):
    """Adds a topic that was never discussed. Must be rejected, not stripped."""

    def generate(self, session_data: SessionData) -> SummaryContent:
        content = super().generate(session_data)
        first = session_data.interactions[0]
        fabricated = Topic(
            topic_id="tupe-transfers",
            label="TUPE transfers",
            interaction_count=1,
            first_discussed_at=first.occurred_at,
            last_discussed_at=first.occurred_at,
        )
        return content.model_copy(
            update={"topics_covered": content.topics_covered + (fabricated,)}
        )


class UngroundedAuthorityGenerator(DeterministicSummaryGenerator):
    """Adds a real, relevant, uncited authority. The most dangerous failure mode.

    Nothing about this authority is false in the abstract - it exists and it
    bears on the topic. It is false only as a statement about this session, and
    that is exactly what the document claims.
    """

    def generate(self, session_data: SessionData) -> SummaryContent:
        content = super().generate(session_data)
        fabricated = Resource(
            resource_id="polkey-v-ae-dayton",
            kind=ResourceKind.CASE_LAW,
            citation="Polkey v AE Dayton Services Ltd [1988] AC 344",
            title="Polkey v AE Dayton Services Ltd",
            cited_in_interaction_ids=(),
            first_cited_at=None,
        )
        return content.model_copy(
            update={"resources_referenced": content.resources_referenced + (fabricated,)}
        )


class UngroundedConceptGenerator(DeterministicSummaryGenerator):
    """Adds a concept the learner never explored."""

    def generate(self, session_data: SessionData) -> SummaryContent:
        content = super().generate(session_data)
        topic_id = content.topics_covered[0].topic_id
        fabricated = Concept(
            concept_id="polkey-reduction",
            label="Polkey reduction",
            explanation="A reduction reflecting the chance of fair dismissal anyway.",
            topic_id=topic_id,
            evidence_interaction_ids=(session_data.interactions[0].interaction_id,),
        )
        return content.model_copy(
            update={"key_concepts": content.key_concepts[:1] + (fabricated,)}
        )


class UngroundedSuggestionGenerator(DeterministicSummaryGenerator):
    """Invents a next step with no gap-report entry and no session topic behind it."""

    def generate(self, session_data: SessionData) -> SummaryContent:
        content = super().generate(session_data)
        fabricated = Suggestion(
            suggestion_id="invented-module-42",
            label="Advanced tribunal advocacy",
            rationale="Looks like a sensible thing to study next.",
            source=SuggestionSource.GAP_REPORT,
        )
        return content.model_copy(update={"next_steps": (fabricated,)})


class PaddingGenerator(DeterministicSummaryGenerator):
    """Pads a single-topic session with a second topic to look fuller."""

    def generate(self, session_data: SessionData) -> SummaryContent:
        content = super().generate(session_data)
        real = content.topics_covered[0]
        padded = Topic(
            topic_id="related-general-contract-law",
            label="Related: general contract law",
            interaction_count=1,
            first_discussed_at=real.first_discussed_at,
            last_discussed_at=real.last_discussed_at,
        )
        return content.model_copy(
            update={"topics_covered": content.topics_covered + (padded,)}
        )


class MissingSectionGenerator(DeterministicSummaryGenerator):
    """Drops Topics Covered from a session that has topic tags."""

    def generate(self, session_data: SessionData) -> SummaryContent:
        content = super().generate(session_data)
        return content.model_copy(update={"topics_covered": (), "key_concepts": ()})


class MalformedGenerator:
    """Returns something that is not :class:`SummaryContent` at all."""

    @classmethod
    def from_settings(cls, settings: object) -> MalformedGenerator:
        return cls()

    def generate(self, session_data: SessionData) -> SummaryContent:
        return {"topics": ["something"]}  # type: ignore[return-value]


class TimeoutGenerator:
    """Always times out."""

    @classmethod
    def from_settings(cls, settings: object) -> TimeoutGenerator:
        return cls()

    def generate(self, session_data: SessionData) -> SummaryContent:
        raise ProviderTimeout(PORT, "scenario_generator_timeout")


class UnavailableGenerator:
    """Always unavailable."""

    @classmethod
    def from_settings(cls, settings: object) -> UnavailableGenerator:
        return cls()

    def generate(self, session_data: SessionData) -> SummaryContent:
        raise ProviderUnavailable(PORT, "scenario_generator_unavailable")


class InvalidResponseGenerator:
    """Answers, but with something the contract rejects."""

    @classmethod
    def from_settings(cls, settings: object) -> InvalidResponseGenerator:
        return cls()

    def generate(self, session_data: SessionData) -> SummaryContent:
        raise ProviderInvalidResponse(PORT, "scenario_generator_invalid")
