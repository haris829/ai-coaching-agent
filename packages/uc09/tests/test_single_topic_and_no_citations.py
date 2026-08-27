"""Two rules about restraint: never pad a topic list, never invent an authority.

Both exist because the tempting failure is the one that makes the document look
better. A single-topic session that lists three topics reads as a fuller
session than it was; an empty Resources section that lists the leading case on
the topic reads as better-evidenced study than took place. Each is a false
statement on a document of professional record.
"""

from __future__ import annotations

from tests.support.documents import pdf_text_normalised, section_html
from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.domain.enums import SourceStatus
from uc09_summary.rendering.html_document import build_html


class TestSingleTopicSessionIsNotPadded:
    def test_one_topic_in_produces_exactly_one_topic_out(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_SINGLE_TOPIC, S.OWNER_USER_ID)

        assert len(record.topics_covered) == 1
        assert record.topics_covered[0].topic_id == "restrictive-covenants"

    def test_the_topic_list_matches_the_tag_record_exactly(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_SINGLE_TOPIC, S.OWNER_USER_ID)

        tagged = {
            tag
            for i in S.INTERACTIONS[S.SESSION_SINGLE_TOPIC]
            for tag in i.topic_tags
        }
        assert {t.topic_id for t in record.topics_covered} == tagged

    def test_key_concepts_expand_instead(self) -> None:
        """Depth, not breadth. The specification is explicit about this."""
        harness = build_harness()
        record = harness.service.generate(S.SESSION_SINGLE_TOPIC, S.OWNER_USER_ID)

        assert len(record.key_concepts) >= 3
        assert all(c.topic_id == "restrictive-covenants" for c in record.key_concepts)

    def test_the_concepts_are_deeper_than_a_multi_topic_session(self) -> None:
        harness = build_harness()
        single = harness.service.generate(S.SESSION_SINGLE_TOPIC, S.OWNER_USER_ID)
        multi = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert all(
            "devoted to this one topic" in c.explanation for c in single.key_concepts
        ), "Every concept in a single-topic session carries the extra depth."
        assert not any(
            "devoted to this one topic" in c.explanation for c in multi.key_concepts
        )

        single_depth = sum(len(c.explanation) for c in single.key_concepts) / len(
            single.key_concepts
        )
        multi_depth = sum(len(c.explanation) for c in multi.key_concepts) / len(
            multi.key_concepts
        )
        assert single_depth > multi_depth, (
            "A single-topic session must put its detail into the concept "
            "explanations, since it cannot put it into more topics."
        )

    def test_the_document_says_why_there_is_only_one_topic(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_SINGLE_TOPIC, S.OWNER_USER_ID)
        html = build_html(record)

        assert "This session covered a single topic." in html
        assert "rather than listing further topics" in html

    def test_nothing_related_is_added_to_the_retrospective_sections(self) -> None:
        """Forward-looking suggestions may name other subjects; a record may not.

        Next Steps legitimately mentions TUPE, because the gap report suggested
        it. The three retrospective sections must mention nothing that was not
        in this session, so the assertion is scoped to them rather than to the
        whole document.
        """
        harness = build_harness()
        record = harness.service.generate(S.SESSION_SINGLE_TOPIC, S.OWNER_USER_ID)
        html = build_html(record)

        for key in ("topics_covered", "key_concepts", "resources_referenced"):
            body = section_html(html, key)
            for absent in ("contract law", "Related:", "TUPE", "Discrimination"):
                assert absent not in body, (
                    f"{absent!r} appears in the retrospective section {key!r} of "
                    "a session that never discussed it."
                )

        assert "TUPE" in section_html(html, "next_steps")


class TestOneInteractionSessionIsNotPadded:
    def test_fewer_than_three_concepts_are_reported_not_invented(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_ONE_INTERACTION, S.OWNER_USER_ID)

        assert len(record.key_concepts) == 1
        assert record.source_status["key_concepts"] is SourceStatus.PARTIAL, (
            "Falling short of the three-to-five target is reported as partial. "
            "It is never resolved by inventing a second and third concept."
        )

    def test_the_document_states_the_shortfall(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_ONE_INTERACTION, S.OWNER_USER_ID)
        html = build_html(record)

        assert "has not been padded" in html


class TestNoCitationsSection:
    def test_the_resources_section_is_empty(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_NO_CITATIONS, S.OWNER_USER_ID)

        assert record.resources_referenced == ()

    def test_the_status_is_empty_not_unavailable(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_NO_CITATIONS, S.OWNER_USER_ID)

        assert record.source_status["citations"] is SourceStatus.EMPTY
        assert record.source_status["resources_referenced"] is SourceStatus.EMPTY

    def test_the_section_says_so_rather_than_disappearing(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_NO_CITATIONS, S.OWNER_USER_ID)
        html = build_html(record)

        assert "<h2>Resources Referenced</h2>" in html
        assert "No legislation or case law was cited during this session." in html

    def test_nothing_is_invented_to_fill_it(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_NO_CITATIONS, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)
        rendered = pdf_text_normalised(result.pdf or b"")

        # A grievance-procedure session has obvious "relevant" authorities.
        # None of them may appear, because none was cited.
        for tempting in ("ACAS", "Employment Rights Act", "[19", "[20", "UKSC", "ICR"):
            assert tempting not in rendered, (
                f"{tempting!r} appears in a session that cited nothing. "
                "Relevance to the topic is not a source."
            )

    def test_the_note_explains_the_restraint(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_NO_CITATIONS, S.OWNER_USER_ID)

        note = record.section_notes["resources_referenced"]
        assert "deliberately not added" in note


class TestUnavailableCitationsAreNotReportedAsNone:
    """``empty`` and ``unavailable`` must never be conflated."""

    def test_status_is_unavailable_when_the_source_failed(self) -> None:
        harness = build_harness()
        record = harness.service.generate(
            S.SESSION_CITATIONS_UNAVAILABLE, S.OWNER_USER_ID
        )

        assert record.resources_referenced == ()
        assert record.source_status["citations"] is SourceStatus.UNAVAILABLE

    def test_a_failed_source_is_distinguishable_from_an_empty_one(self) -> None:
        harness = build_harness()
        empty = harness.service.generate(S.SESSION_NO_CITATIONS, S.OWNER_USER_ID)
        failed = harness.service.generate(
            S.SESSION_CITATIONS_UNAVAILABLE, S.OWNER_USER_ID
        )

        assert empty.source_status["citations"] is SourceStatus.EMPTY
        assert failed.source_status["citations"] is SourceStatus.UNAVAILABLE
        assert empty.source_status["citations"] != failed.source_status["citations"]
