"""Partial summaries: flagged, time-stamped, and never presented as complete.

This matters because the PDF is evidence. A partial summary passed off as
complete misrepresents the session length to whoever reads it - a regulator
assessing whether a solicitor did the hours. So the marker is asserted in both
output forms, and there are explicit tests that the complete-record wording is
absent.
"""

from __future__ import annotations

from datetime import timedelta

from tests.support.documents import pdf_text_normalised
from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.adapters.mock.clock import DEFAULT_NOW
from uc09_summary.adapters.mock.generator import UnavailableGenerator
from uc09_summary.domain.enums import SourceStatus
from uc09_summary.rendering.html_document import (
    COMPLETE_MARKER,
    PARTIAL_MARKER,
    build_html,
)


class TestPartialFlagAndTimestamp:
    def test_an_in_progress_session_produces_a_partial_summary(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)

        assert record.is_partial is True

    def test_covers_interactions_through_is_the_generation_moment(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)

        assert record.covers_interactions_through == DEFAULT_NOW
        assert record.generated_at == DEFAULT_NOW

    def test_interactions_after_the_cover_moment_are_excluded(self) -> None:
        """A partial summary covers what had happened, not what happens later."""
        harness = build_harness()
        harness.clock.set(S.BASE + timedelta(minutes=10))
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)

        within = [
            i
            for i in S.INTERACTIONS[S.SESSION_IN_PROGRESS]
            if i.occurred_at <= S.BASE + timedelta(minutes=10)
        ]
        counted = sum(t.interaction_count for t in record.topics_covered)
        assert counted == sum(len(i.topic_tags) for i in within)
        assert record.source_status["interactions"] is SourceStatus.PARTIAL

    def test_a_complete_session_is_not_partial(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert record.is_partial is False
        assert record.covers_interactions_through == S.SESSIONS[S.SESSION_COMPLETE].ended_at

    def test_the_duration_of_a_partial_summary_is_elapsed_time(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)

        expected = int((DEFAULT_NOW - S.BASE).total_seconds())
        assert record.session_duration_seconds == expected


class TestPartialMarkerInBothOutputForms:
    """The marker must appear in the HTML *and* in the rendered PDF."""

    def _partial(self):
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)
        return harness, record

    def test_marker_present_in_html(self) -> None:
        _, record = self._partial()
        assert PARTIAL_MARKER in build_html(record)

    def test_marker_present_in_the_pdf_bytes(self) -> None:
        harness, record = self._partial()
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert result.pdf is not None
        assert PARTIAL_MARKER in pdf_text_normalised(result.pdf)

    def test_marker_present_in_the_html_fallback_when_the_pdf_fails(self) -> None:
        from uc09_summary.adapters.mock.renderer import FailingDocumentRenderer

        harness = build_harness(
            overrides={"document_renderer": FailingDocumentRenderer()}
        )
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert result.pdf_available is False
        assert PARTIAL_MARKER in result.html

    def test_marker_appears_in_more_than_one_place_in_the_document(self) -> None:
        _, record = self._partial()
        html = build_html(record)
        assert html.count(PARTIAL_MARKER) >= 2, (
            "The partial marker appears in the banner, in the verification "
            "block and in the footer, so that a printed page separated from "
            "its first sheet still says what it is."
        )

    def test_the_covering_instant_is_printed_in_both_forms(self) -> None:
        harness, record = self._partial()
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)
        stamp = record.covers_interactions_through.strftime("%Y-%m-%d %H:%M:%S UTC")

        assert stamp in result.canonical_html
        assert stamp in pdf_text_normalised(result.pdf or b"")

    def test_the_api_exposes_the_marker_for_a_frontend(self) -> None:
        harness, record = self._partial()
        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers=harness.as_user(S.OWNER_USER_ID),
        )
        body = response.json()

        assert body["is_partial"] is True
        assert body["partial_marker"] == PARTIAL_MARKER


class TestAPartialSummaryIsNeverLabelledComplete:
    def test_the_complete_marker_is_absent_from_a_partial_html_document(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)
        html = build_html(record)

        assert COMPLETE_MARKER not in html, (
            "A partial summary must never carry the complete-record wording."
        )

    def test_the_complete_marker_is_absent_from_a_partial_pdf(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert COMPLETE_MARKER not in pdf_text_normalised(result.pdf or b"")

    def test_the_document_states_the_limit_of_what_it_covers(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)
        html = build_html(record)

        assert "is not a complete record of the session" in html

    def test_the_duration_is_labelled_as_elapsed_not_as_a_session_length(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)
        rendered = pdf_text_normalised(result.pdf or b"")

        assert "elapsed at the time of this partial summary" in rendered, (
            "An elapsed time on a partial record is not a session length, and "
            "the document must not let a reader take it for one."
        )

    def test_a_complete_summary_carries_the_complete_marker_and_not_the_partial_one(
        self,
    ) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)
        rendered = pdf_text_normalised(result.pdf or b"")

        assert COMPLETE_MARKER in rendered
        assert PARTIAL_MARKER not in rendered


class TestPartialFallbackCombination:
    """A partial summary that also fell back must say both things."""

    def test_both_conditions_are_stated(self) -> None:
        harness = build_harness(
            overrides={"summary_generator": UnavailableGenerator()}
        )
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)
        rendered = pdf_text_normalised(result.pdf or b"")

        assert PARTIAL_MARKER in rendered
        assert "not a full summary" in rendered
        assert COMPLETE_MARKER not in rendered
