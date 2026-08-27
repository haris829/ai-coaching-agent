"""Application logs record identifiers, counts and timing - never content.

Not summary content, not question text, and not which topics a named learner
explored. That last one is the reason the rule is stricter than it first looks:
a log line pairing a learner with "unfair dismissal" discloses something about
that person, even though nothing in it is a summary.

The suite drives the whole flow - generate, preview, download, plus every
failure path - and searches the captured log output for every piece of content
the session contains.
"""

from __future__ import annotations

import json

import pytest
import structlog

from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.adapters.mock.generator import (
    UnavailableGenerator,
    UngroundedTopicGenerator,
)
from uc09_summary.adapters.mock.renderer import FailingDocumentRenderer
from uc09_summary.logging_setup import DENIED_LOG_KEYS


@pytest.fixture
def captured():
    """Capture structlog events for the duration of a test."""
    from structlog.testing import LogCapture

    capture = LogCapture()
    old = structlog.get_config()["processors"]
    structlog.configure(processors=[capture])
    try:
        yield capture
    finally:
        structlog.configure(processors=old)


def _log_text(capture) -> str:
    return json.dumps(capture.entries, default=str)


def _full_flow(harness) -> object:
    record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
    harness.service.preview_html(record.summary_id, S.OWNER_USER_ID)
    harness.service.export(record.summary_id, S.OWNER_USER_ID)
    return record


class TestContentNeverReachesTheLogs:
    def test_question_text_is_absent(self, captured) -> None:
        harness = build_harness()
        _full_flow(harness)
        text = _log_text(captured)

        for interaction in S.INTERACTIONS[S.SESSION_COMPLETE]:
            assert interaction.question_text not in text

    def test_topic_labels_are_absent(self, captured) -> None:
        harness = build_harness()
        record = _full_flow(harness)
        text = _log_text(captured)

        for topic in record.topics_covered:
            assert topic.label not in text
            assert topic.topic_id not in text, (
                "A topic identifier beside a session identifier still says "
                "what a named learner studied."
            )

    def test_concept_labels_and_explanations_are_absent(self, captured) -> None:
        harness = build_harness()
        record = _full_flow(harness)
        text = _log_text(captured)

        for concept in record.key_concepts:
            assert concept.label not in text
            assert concept.explanation not in text
            assert concept.concept_id not in text

    def test_resource_titles_and_citations_are_absent(self, captured) -> None:
        harness = build_harness()
        record = _full_flow(harness)
        text = _log_text(captured)

        for resource in record.resources_referenced:
            assert resource.title not in text
            assert resource.citation not in text

    def test_next_step_labels_are_absent(self, captured) -> None:
        harness = build_harness()
        record = _full_flow(harness)
        text = _log_text(captured)

        for suggestion in record.next_steps:
            assert suggestion.label not in text

    def test_the_learner_name_is_absent(self, captured) -> None:
        harness = build_harness()
        record = _full_flow(harness)

        assert record.user_display_name not in _log_text(captured)

    def test_no_html_or_pdf_body_is_logged(self, captured) -> None:
        harness = build_harness()
        _full_flow(harness)
        text = _log_text(captured)

        assert "<html" not in text
        assert "CPD Learning Evidence" not in text
        assert "%PDF" not in text


class TestOperationalFactsAreLogged:
    def test_identifiers_counts_and_timing_are_recorded(self, captured) -> None:
        harness = build_harness()
        record = _full_flow(harness)
        events = {e["event"]: e for e in captured.entries}

        generated = events["summary_generated"]
        assert generated["summary_id"] == record.summary_id
        assert generated["session_id"] == record.session_id
        assert generated["topic_count"] == len(record.topics_covered)
        assert generated["concept_count"] == len(record.key_concepts)
        assert generated["resource_count"] == len(record.resources_referenced)
        assert generated["next_step_count"] == len(record.next_steps)
        assert generated["duration_seconds"] == record.session_duration_seconds

    def test_previews_and_downloads_are_recorded(self, captured) -> None:
        harness = build_harness()
        _full_flow(harness)
        names = {e["event"] for e in captured.entries}

        assert "summary_previewed" in names
        assert "summary_downloaded" in names


class TestFailurePathsAlsoStayQuiet:
    def test_a_grounding_rejection_logs_reasons_without_identifiers(
        self, captured
    ) -> None:
        harness = build_harness(
            overrides={"summary_generator": UngroundedTopicGenerator()}
        )
        harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        events = {e["event"]: e for e in captured.entries}

        rejection = events["generated_summary_rejected_ungrounded"]
        assert rejection["violation_count"] >= 1
        assert rejection["action"] == "rejected_whole_response"
        assert "tupe-transfers" not in json.dumps(rejection, default=str), (
            "The rejection must be loud about what happened and silent about "
            "what the learner was studying."
        )
        assert any("not present in session topic tags" in r for r in rejection["violation_reasons"])

    def test_a_generator_outage_logs_a_code_not_a_message(self, captured) -> None:
        harness = build_harness(overrides={"summary_generator": UnavailableGenerator()})
        harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        events = {e["event"]: e for e in captured.entries}

        failure = events["summary_generation_failed"]
        assert failure["error_code"] == "provider_unavailable"
        assert failure["port"] == "summary_generator"

    def test_a_renderer_failure_logs_no_document(self, captured) -> None:
        harness = build_harness(
            overrides={"document_renderer": FailingDocumentRenderer()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        harness.service.export(record.summary_id, S.OWNER_USER_ID)
        text = _log_text(captured)

        assert "pdf_render_failed" in text
        assert "<html" not in text
        assert record.user_display_name not in text

    def test_an_ownership_denial_is_recorded_without_content(self, captured) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        try:
            harness.service.get(record.summary_id, S.OTHER_USER_ID)
        except Exception:
            pass
        events = {e["event"]: e for e in captured.entries}

        denial = events["summary_access_denied"]
        assert denial["reason"] == "requester_is_not_summary_owner"
        assert denial["summary_id"] == record.summary_id


class TestTheRedactionGuard:
    """A privacy rule that relies only on discipline is one log line from false."""

    def test_a_denied_key_is_dropped_even_if_a_call_site_passes_it(self) -> None:
        from uc09_summary.logging_setup import _redaction_processor

        event = _redaction_processor(
            None,
            "info",
            {"event": "x", "summary_id": "sum_1", "question_text": "secret question"},
        )

        assert event["summary_id"] == "sum_1"
        assert event["question_text"] == "[redacted]"

    def test_the_deny_list_covers_every_content_bearing_field(self) -> None:
        for key in (
            "question_text",
            "topics",
            "concepts",
            "citation",
            "html",
            "user_display_name",
            "section_notes",
        ):
            assert key in DENIED_LOG_KEYS

    def test_redaction_is_case_insensitive(self) -> None:
        from uc09_summary.logging_setup import _redaction_processor

        event = _redaction_processor(None, "info", {"Question_Text": "secret"})
        assert event["Question_Text"] == "[redacted]"
