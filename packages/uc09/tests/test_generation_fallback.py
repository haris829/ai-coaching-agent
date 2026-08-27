"""Generation failure falls back to a structured log of questions asked.

Never produce nothing, and never present a fallback as a full summary. Both
halves are tested: the learner always gets a document, and the document always
says what it is.
"""

from __future__ import annotations

import pytest

from tests.support.documents import pdf_text_normalised
from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.adapters.mock.generator import (
    InvalidResponseGenerator,
    MalformedGenerator,
    TimeoutGenerator,
    UnavailableGenerator,
    UngroundedTopicGenerator,
)
from uc09_summary.domain.enums import GenerationMode, SourceStatus
from uc09_summary.rendering.html_document import build_html

FAILING_GENERATORS = {
    "unavailable": UnavailableGenerator,
    "timeout": TimeoutGenerator,
    "invalid_response": InvalidResponseGenerator,
    "malformed": MalformedGenerator,
}


@pytest.mark.parametrize("name", sorted(FAILING_GENERATORS))
class TestQuestionLogFallback:
    def _record(self, name: str, session_id: str = S.SESSION_COMPLETE):
        harness = build_harness(
            overrides={"summary_generator": FAILING_GENERATORS[name]()}
        )
        return harness, harness.service.generate(session_id, S.OWNER_USER_ID)

    def test_something_is_always_produced(self, name: str) -> None:
        _, record = self._record(name)
        assert record.summary_id

    def test_it_is_marked_as_the_fallback(self, name: str) -> None:
        _, record = self._record(name)
        assert record.generation_mode is GenerationMode.QUESTION_LOG_FALLBACK

    def test_the_question_log_holds_the_questions_asked(self, name: str) -> None:
        _, record = self._record(name)
        expected = S.INTERACTIONS[S.SESSION_COMPLETE]

        assert len(record.question_log) == len(expected)
        assert [e.interaction_id for e in record.question_log] == [
            i.interaction_id for i in expected
        ]
        assert [e.question_text for e in record.question_log] == [
            i.question_text for i in expected
        ]

    def test_key_concepts_are_absent_rather_than_guessed(self, name: str) -> None:
        _, record = self._record(name)
        assert record.key_concepts == ()
        assert record.source_status["key_concepts"] is SourceStatus.UNAVAILABLE
        assert "have not been inferred" in record.section_notes["key_concepts"]

    def test_topics_and_citations_survive_because_they_need_no_generation(
        self, name: str
    ) -> None:
        """A direct read of the tag and citation records is still grounded."""
        _, record = self._record(name)

        tagged = {
            tag for i in S.INTERACTIONS[S.SESSION_COMPLETE] for tag in i.topic_tags
        }
        assert {t.topic_id for t in record.topics_covered} == tagged
        assert {r.resource_id for r in record.resources_referenced} == {
            r.resource_id for r in S.CITATIONS[S.SESSION_COMPLETE]
        }

    def test_it_is_never_presented_as_a_full_summary(self, name: str) -> None:
        _, record = self._record(name)
        html = build_html(record)

        assert "not a full summary" in html
        assert "Automatic summary generation was unavailable" in html

    def test_the_marker_reaches_the_pdf(self, name: str) -> None:
        harness, record = self._record(name)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert "not a full summary" in pdf_text_normalised(result.pdf or b"")

    def test_the_questions_are_rendered_in_the_document(self, name: str) -> None:
        harness, record = self._record(name)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)
        rendered = pdf_text_normalised(result.pdf or b"")

        assert "Questions Asked" in rendered
        for interaction in S.INTERACTIONS[S.SESSION_COMPLETE]:
            assert interaction.question_text in rendered


class TestFallbackDistinguishesFailureFromFabrication:
    def test_an_outage_is_recorded_as_unavailable(self) -> None:
        harness = build_harness(overrides={"summary_generator": UnavailableGenerator()})
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert record.source_status["summary_generator"] is SourceStatus.UNAVAILABLE
        assert "Automatic summary generation was unavailable" in record.section_notes[
            "generation"
        ]

    def test_a_rejection_is_recorded_as_invalid(self) -> None:
        harness = build_harness(
            overrides={"summary_generator": UngroundedTopicGenerator()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert record.source_status["summary_generator"] is SourceStatus.INVALID
        assert "could not be traced" in record.section_notes["generation"]

    def test_the_two_notes_are_different(self) -> None:
        outage = build_harness(
            overrides={"summary_generator": UnavailableGenerator()}
        ).service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        rejection = build_harness(
            overrides={"summary_generator": UngroundedTopicGenerator()}
        ).service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert (
            outage.section_notes["generation"]
            != rejection.section_notes["generation"]
        ), (
            "Collapsing a fabrication into an outage would hide the more "
            "serious of the two."
        )


class TestGapReportDegradation:
    def test_an_unavailable_gap_report_degrades_to_session_derived_steps(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_GAP_UNAVAILABLE, S.USER_GAP_UNAVAILABLE)

        assert record.source_status["gap_report"] is SourceStatus.UNAVAILABLE
        assert record.next_steps
        assert all(s.source.value == "session_content" for s in record.next_steps)
        assert all(s.related_topic_id for s in record.next_steps)

    def test_the_document_says_the_gap_report_was_unavailable(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_GAP_UNAVAILABLE, S.USER_GAP_UNAVAILABLE)

        assert "gap report was unavailable" in record.section_notes["next_steps"]

    def test_an_empty_gap_report_is_reported_as_empty_not_unavailable(self) -> None:
        harness = build_harness()
        record = harness.service.generate(
            S.SESSION_NO_GAP_SUGGESTIONS, S.USER_NO_GAP_SUGGESTIONS
        )

        assert record.source_status["gap_report"] is SourceStatus.EMPTY
        assert "returned no suggestions" in record.section_notes["next_steps"]

    def test_nothing_is_invented_when_neither_source_yields_a_step(self) -> None:
        """No interactions and no gap suggestions: the section is omitted, not filled."""
        harness = build_harness()
        record = harness.service.generate(
            S.SESSION_NOTHING_TO_REPORT, S.USER_NO_GAP_SUGGESTIONS
        )

        assert record.next_steps == ()
        assert "Nothing has been inferred" in record.section_notes["next_steps"]
        assert "No next step could be grounded" in build_html(record)


class TestUpstreamFailuresDoNotLoseTheSummary:
    def test_an_interaction_outage_still_produces_a_record(self) -> None:
        harness = build_harness()
        record = harness.service.generate(
            S.SESSION_INTERACTIONS_UNAVAILABLE, S.OWNER_USER_ID
        )

        assert record.summary_id
        assert record.source_status["interactions"] is SourceStatus.UNAVAILABLE
        assert record.topics_covered == ()

    def test_a_session_outage_is_the_one_failure_that_stops_the_request(self) -> None:
        from uc09_summary.domain.errors import ProviderUnavailable

        harness = build_harness()
        with pytest.raises(ProviderUnavailable):
            harness.service.generate(S.SESSION_UNAVAILABLE, S.OWNER_USER_ID)

    def test_that_failure_is_a_503_with_no_internal_detail(self) -> None:
        harness = build_harness()
        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_UNAVAILABLE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        )

        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "upstream_unavailable"
        assert "scenario" not in response.text
        assert "Mock" not in response.text
