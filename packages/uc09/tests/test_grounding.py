"""Grounding: every claim in the document is true of the session it describes.

This is the centre of the suite. A CPD evidence document carries a learner
name and may be shown to a regulator; a section listing a topic that was not
discussed, or an authority that was not cited, is a false record with that
name on it.

The tests here assert three things:

1. Each individual grounding rule rejects its own violation.
2. Rejection is **whole**. A response with one fabricated element is discarded
   entirely - not stripped of the bad part and stored.
3. **Exhaustively**, nothing reaches a rendered document without a source in
   session data. :class:`TestNothingUngroundedReachesADocument` walks every
   element of a stored summary and every text block of the rendered HTML and
   PDF, and traces each back to a session record.
"""

from __future__ import annotations

import pytest

from tests.support.documents import pdf_text_normalised, summary_from_content
from tests.support.factories import (
    make_session_data,
    multi_topic_session_data,
    no_citation_session_data,
    one_interaction_session_data,
    single_topic_session_data,
)
from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.adapters.mock.generator import (
    DeterministicSummaryGenerator,
    MissingSectionGenerator,
    PaddingGenerator,
    UngroundedAuthorityGenerator,
    UngroundedConceptGenerator,
    UngroundedSuggestionGenerator,
    UngroundedTopicGenerator,
)
from uc09_summary.adapters.real.pdf_renderer import SimplePdfRenderer
from uc09_summary.domain.enums import (
    GenerationMode,
    ResourceKind,
    SourceStatus,
    SuggestionSource,
)
from uc09_summary.domain.errors import GroundingViolation
from uc09_summary.domain.grounding import check_grounding
from uc09_summary.domain.models import Resource, Suggestion, SummaryContent, Topic
from uc09_summary.rendering.html_document import build_html
from uc09_summary.rendering.text_extract import extract_text_blocks

# --------------------------------------------------------------------------
# Each rule rejects its own violation
# --------------------------------------------------------------------------


class TestTopicGrounding:
    """Topics Covered lists only topics actually discussed."""

    def test_a_topic_not_in_the_tag_record_is_rejected(self) -> None:
        data = multi_topic_session_data()
        content = UngroundedTopicGenerator().generate(data)

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)

        assert any("not present in session topic tags" in v for v in caught.value.violations)

    def test_a_related_topic_is_still_ungrounded(self) -> None:
        """Relevance is not a source. "Related to" is not a source."""
        data = single_topic_session_data()
        content = PaddingGenerator().generate(data)

        with pytest.raises(GroundingViolation):
            check_grounding(content, data)

    def test_a_miscounted_topic_is_rejected(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        inflated = good.topics_covered[0].model_copy(update={"interaction_count": 99})
        content = good.model_copy(
            update={"topics_covered": (inflated,) + good.topics_covered[1:]}
        )

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("interaction_count" in v for v in caught.value.violations)

    def test_a_shifted_discussion_window_is_rejected(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        shifted = good.topics_covered[0].model_copy(
            update={"last_discussed_at": data.session.started_at}
        )
        content = good.model_copy(
            update={"topics_covered": (shifted,) + good.topics_covered[1:]}
        )

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("discussion window" in v for v in caught.value.violations)

    def test_a_grounded_topic_list_passes(self) -> None:
        data = multi_topic_session_data()
        check_grounding(DeterministicSummaryGenerator().generate(data), data)


class TestAuthorityGrounding:
    """Resources Referenced lists only authorities actually cited in the session."""

    def test_an_uncited_authority_is_rejected(self) -> None:
        data = multi_topic_session_data()
        content = UngroundedAuthorityGenerator().generate(data)

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)

        assert any("not cited during this session" in v for v in caught.value.violations)

    def test_a_real_and_relevant_but_uncited_authority_is_still_rejected(self) -> None:
        """The dangerous case: true in the abstract, false about this session."""
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        genuine_leading_case = Resource(
            resource_id="polkey-v-ae-dayton",
            kind=ResourceKind.CASE_LAW,
            citation="Polkey v AE Dayton Services Ltd [1988] AC 344",
            title="Polkey v AE Dayton Services Ltd",
            cited_in_interaction_ids=(),
        )
        content = good.model_copy(
            update={"resources_referenced": good.resources_referenced + (genuine_leading_case,)}
        )

        with pytest.raises(GroundingViolation):
            check_grounding(content, data)

    def test_an_altered_citation_string_is_rejected(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        altered = good.resources_referenced[0].model_copy(
            update={"citation": "Employment Rights Act 1996, s 94"}
        )
        content = good.model_copy(
            update={"resources_referenced": (altered,) + good.resources_referenced[1:]}
        )

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("citation text differs" in v for v in caught.value.violations)

    def test_a_recharacterised_authority_is_rejected(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        recharacterised = good.resources_referenced[0].model_copy(
            update={"kind": ResourceKind.CASE_LAW}
        )
        content = good.model_copy(
            update={"resources_referenced": (recharacterised,) + good.resources_referenced[1:]}
        )

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("kind differs" in v for v in caught.value.violations)

    def test_whitespace_differences_are_tolerated(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        respaced = good.resources_referenced[0]
        respaced = respaced.model_copy(
            update={"citation": f"  {respaced.citation.replace(' ', '  ')}  "}
        )
        content = good.model_copy(
            update={"resources_referenced": (respaced,) + good.resources_referenced[1:]}
        )
        check_grounding(content, data)


class TestConceptGrounding:
    def test_a_concept_not_in_the_tag_record_is_rejected(self) -> None:
        data = multi_topic_session_data()
        content = UngroundedConceptGenerator().generate(data)

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("not present in session concept tags" in v for v in caught.value.violations)

    def test_a_concept_citing_an_unknown_interaction_is_rejected(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        forged = good.key_concepts[0].model_copy(
            update={"evidence_interaction_ids": ("interaction-that-never-happened",)}
        )
        content = good.model_copy(update={"key_concepts": (forged,) + good.key_concepts[1:]})

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("not in the session record" in v for v in caught.value.violations)

    def test_a_concept_attached_to_an_ungrounded_topic_is_rejected(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        misattached = good.key_concepts[0].model_copy(update={"topic_id": "tupe-transfers"})
        content = good.model_copy(
            update={"key_concepts": (misattached,) + good.key_concepts[1:]}
        )

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("not grounded in this session" in v for v in caught.value.violations)

    def test_more_than_five_concepts_is_rejected(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        extra = good.key_concepts[0].model_copy(update={"concept_id": "qualifying-period"})
        content = good.model_copy(update={"key_concepts": good.key_concepts + (extra,)})

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("exceeds the maximum" in v for v in caught.value.violations)


class TestNextStepGrounding:
    def test_an_invented_gap_suggestion_is_rejected(self) -> None:
        data = multi_topic_session_data()
        content = UngroundedSuggestionGenerator().generate(data)

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("not present in the gap report" in v for v in caught.value.violations)

    def test_claiming_gap_provenance_when_the_report_was_unavailable_is_rejected(
        self,
    ) -> None:
        data = make_session_data(
            interactions=S.INTERACTIONS[S.SESSION_COMPLETE],
            citations=(),
            gap_suggestions=None,
        )
        content = SummaryContent(
            topics_covered=DeterministicSummaryGenerator()
            .generate(data)
            .topics_covered,
            next_steps=(
                Suggestion(
                    suggestion_id="gap-tupe-basics",
                    label="TUPE",
                    source=SuggestionSource.GAP_REPORT,
                ),
            ),
        )

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("gap report was unavailable" in v for v in caught.value.violations)

    def test_a_session_derived_step_needs_a_topic_actually_discussed(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        content = good.model_copy(
            update={
                "next_steps": (
                    Suggestion(
                        suggestion_id="session-tupe",
                        label="TUPE",
                        source=SuggestionSource.SESSION_CONTENT,
                        related_topic_id="tupe-transfers",
                    ),
                )
            }
        )

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)
        assert any("not a topic discussed in this session" in v for v in caught.value.violations)

    def test_a_session_derived_step_without_a_topic_is_rejected(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        content = good.model_copy(
            update={
                "next_steps": (
                    Suggestion(
                        suggestion_id="session-vague",
                        label="Keep studying",
                        source=SuggestionSource.SESSION_CONTENT,
                        related_topic_id=None,
                    ),
                )
            }
        )

        with pytest.raises(GroundingViolation):
            check_grounding(content, data)


class TestViolationReporting:
    def test_all_violations_are_reported_not_just_the_first(self) -> None:
        data = multi_topic_session_data()
        good = DeterministicSummaryGenerator().generate(data)
        content = good.model_copy(
            update={
                "topics_covered": good.topics_covered
                + (
                    Topic(
                        topic_id="fabricated-a",
                        label="A",
                        interaction_count=1,
                        first_discussed_at=data.session.started_at,
                        last_discussed_at=data.session.started_at,
                    ),
                    Topic(
                        topic_id="fabricated-b",
                        label="B",
                        interaction_count=1,
                        first_discussed_at=data.session.started_at,
                        last_discussed_at=data.session.started_at,
                    ),
                ),
                "resources_referenced": good.resources_referenced
                + (
                    Resource(
                        resource_id="never-cited",
                        kind=ResourceKind.CASE_LAW,
                        citation="Some v Case [2020] UKSC 1",
                        title="Some v Case",
                    ),
                ),
            }
        )

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)

        assert len(caught.value.violations) >= 3, (
            "One rejection must report the full extent of the problem, so that "
            "a fabricating generator is diagnosed once rather than repeatedly."
        )

    def test_log_safe_reasons_carry_no_identifiers(self) -> None:
        """The logged form must not disclose what a named learner studied."""
        data = multi_topic_session_data()
        content = UngroundedConceptGenerator().generate(data)

        with pytest.raises(GroundingViolation) as caught:
            check_grounding(content, data)

        joined = " ".join(caught.value.reasons)
        assert "polkey-reduction" not in joined
        for concept in data.concept_ids:
            assert concept not in joined
        assert caught.value.reasons, "Reasons must still say what went wrong."


# --------------------------------------------------------------------------
# Rejection is whole, and visible
# --------------------------------------------------------------------------


class TestRejectionIsWholeNotStripped:
    """An ungrounded response is discarded entirely, and the failure is visible."""

    @pytest.mark.parametrize(
        "generator",
        [
            UngroundedTopicGenerator(),
            UngroundedAuthorityGenerator(),
            UngroundedConceptGenerator(),
            UngroundedSuggestionGenerator(),
        ],
        ids=["topic", "authority", "concept", "suggestion"],
    )
    def test_no_part_of_a_rejected_response_is_stored(self, generator: object) -> None:
        harness = build_harness(overrides={"summary_generator": generator})
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert record.generation_mode is GenerationMode.QUESTION_LOG_FALLBACK, (
            "A rejected response must not be stored as a generated summary."
        )
        assert record.key_concepts == (), (
            "The generated body is discarded whole. Keeping the parts that "
            "happened to be grounded is the silent strip this rule forbids."
        )
        assert record.source_status["summary_generator"] is SourceStatus.INVALID, (
            "The generator answered and was refused. That is 'invalid', not "
            "'unavailable' - the vocabulary distinguishes them and so must the "
            "record."
        )

    def test_the_fabricated_authority_reaches_no_output_at_all(self) -> None:
        harness = build_harness(
            overrides={"summary_generator": UngroundedAuthorityGenerator()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        forbidden = "Polkey"
        assert all(forbidden not in r.title for r in record.resources_referenced)
        assert forbidden not in result.canonical_html
        assert forbidden not in result.html
        assert forbidden not in pdf_text_normalised(result.pdf or b"")

    def test_the_fabricated_topic_reaches_no_output_at_all(self) -> None:
        harness = build_harness(
            overrides={"summary_generator": UngroundedTopicGenerator()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert all(t.topic_id != "tupe-transfers" for t in record.topics_covered)
        assert "TUPE transfers" not in result.canonical_html
        assert "TUPE transfers" not in pdf_text_normalised(result.pdf or b"")

    def test_rejection_is_marked_on_the_record_for_a_reader_to_see(self) -> None:
        harness = build_harness(
            overrides={"summary_generator": UngroundedTopicGenerator()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        html = build_html(record)

        assert "could not be traced to the session record" in record.section_notes["generation"]
        assert "not a full summary" in html, (
            "A fallback is never presented as a full summary."
        )

    def test_a_missing_section_from_a_session_with_tags_is_rejected(self) -> None:
        harness = build_harness(
            overrides={"summary_generator": MissingSectionGenerator()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        assert record.generation_mode is GenerationMode.QUESTION_LOG_FALLBACK


# --------------------------------------------------------------------------
# Exhaustive: nothing reaches a document without a source
# --------------------------------------------------------------------------


def _source_vocabulary(data) -> set[str]:
    """Every string a document is allowed to derive content from."""
    allowed: set[str] = set()
    for interaction in data.interactions:
        allowed.add(interaction.interaction_id)
        allowed.update(interaction.topic_tags)
        allowed.update(interaction.concept_tags)
    for resource in data.citations:
        allowed.add(resource.resource_id)
        allowed.add(resource.citation)
        allowed.add(resource.title)
    for suggestion in data.gap_suggestions or ():
        allowed.add(suggestion.suggestion_id)
        allowed.add(suggestion.label)
    return allowed


class TestNothingUngroundedReachesADocument:
    """Walk every element, and every rendered block, back to a session record."""

    @pytest.mark.parametrize(
        "case",
        [
            multi_topic_session_data,
            single_topic_session_data,
            no_citation_session_data,
            one_interaction_session_data,
        ],
        ids=["multi_topic", "single_topic", "no_citations", "one_interaction"],
    )
    def test_every_stored_element_traces_to_a_session_record(self, case) -> None:
        data = case()
        content = DeterministicSummaryGenerator().generate(data)
        check_grounding(content, data)

        topic_tags = set(data.topic_ids)
        concept_tags = set(data.concept_ids)
        interaction_ids = data.interaction_ids
        citation_ids = {r.resource_id for r in data.citations}
        gap_ids = {s.suggestion_id for s in (data.gap_suggestions or ())}

        for topic in content.topics_covered:
            assert topic.topic_id in topic_tags

        for concept in content.key_concepts:
            assert concept.concept_id in concept_tags
            assert concept.topic_id in topic_tags
            assert set(concept.evidence_interaction_ids) <= interaction_ids

        for resource in content.resources_referenced:
            assert resource.resource_id in citation_ids

        for suggestion in content.next_steps:
            if suggestion.source is SuggestionSource.GAP_REPORT:
                assert suggestion.suggestion_id in gap_ids
            else:
                assert suggestion.related_topic_id in topic_tags

    @pytest.mark.parametrize(
        "case",
        [multi_topic_session_data, single_topic_session_data, no_citation_session_data],
        ids=["multi_topic", "single_topic", "no_citations"],
    )
    def test_no_authority_like_string_appears_that_was_not_cited(self, case) -> None:
        """Scan the rendered document for citation-shaped text and check each one.

        The check that would have caught a fabricated authority even if the
        structured check somehow missed it: anything in the document that looks
        like a legal citation must appear in the session citation record.
        """
        import re

        data = case()
        content = DeterministicSummaryGenerator().generate(data)
        record = summary_from_content(data, content)
        html = build_html(record)
        text = " ".join(extract_text_blocks(html))
        rendered = pdf_text_normalised(SimplePdfRenderer().html_to_pdf(html))

        cited = " ".join(r.citation + " " + r.title for r in data.citations)
        # Neutral citation, law report reference, or a statute section.
        pattern = re.compile(r"\[\d{4}\]\s+\w+\s+\d+|\b[A-Z][a-z]+ Act \d{4}(, s(ection)? \d+)?")

        for haystack, label in ((text, "HTML"), (rendered, "PDF")):
            for match in pattern.findall(haystack):
                fragment = match if isinstance(match, str) else match[0]
                if not fragment.strip():
                    continue
                assert fragment in cited, (
                    f"{label} contains citation-shaped text {fragment!r} that is "
                    "not in the session citation record."
                )

    def test_every_topic_and_concept_label_in_the_pdf_derives_from_a_tag(self) -> None:
        data = multi_topic_session_data()
        content = DeterministicSummaryGenerator().generate(data)
        record = summary_from_content(data, content)
        rendered = pdf_text_normalised(SimplePdfRenderer().html_to_pdf(build_html(record)))
        allowed = _source_vocabulary(data)

        for topic in record.topics_covered:
            assert topic.label in rendered
            assert topic.topic_id in allowed
        for concept in record.key_concepts:
            assert concept.label in rendered
            assert concept.concept_id in allowed

    def test_an_empty_session_produces_a_document_that_claims_nothing(self) -> None:
        data = make_session_data(interactions=(), citations=(), gap_suggestions=None)
        content = DeterministicSummaryGenerator().generate(data)
        check_grounding(content, data)
        record = summary_from_content(data, content)
        html = build_html(record)

        assert content.topics_covered == ()
        assert content.key_concepts == ()
        assert content.resources_referenced == ()
        assert content.next_steps == ()
        assert "No topic was recorded as discussed in this session." in html
        assert "No legislation or case law was cited during this session." in html
