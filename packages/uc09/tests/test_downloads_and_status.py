"""Download logging and the session status transition."""

from __future__ import annotations

from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.adapters.mock.renderer import FailingDocumentRenderer
from uc09_summary.domain.enums import SessionStatus


class TestDownloadsAreLogged:
    def test_a_download_is_recorded_against_the_session(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        harness.service.export(record.summary_id, S.OWNER_USER_ID)

        events = harness.downloads.for_session(record.session_id)
        assert len(events) == 1
        assert events[0].session_id == record.session_id
        assert events[0].summary_id == record.summary_id
        assert events[0].user_id == S.OWNER_USER_ID

    def test_exactly_one_event_per_download(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        for _ in range(3):
            harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert len(harness.downloads.for_summary(record.summary_id)) == 3, (
            "The log answers how many times the evidence was taken away, so "
            "repeat downloads are recorded, never deduplicated."
        )

    def test_each_event_has_a_distinct_identifier(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        for _ in range(3):
            harness.service.export(record.summary_id, S.OWNER_USER_ID)

        ids = [e.download_id for e in harness.downloads.for_summary(record.summary_id)]
        assert len(set(ids)) == 3

    def test_the_event_records_the_format_and_size(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        event = harness.downloads.for_summary(record.summary_id)[0]
        assert event.format == "pdf"
        assert event.pdf_available is True
        assert event.byte_count == len(result.pdf or b"")

    def test_a_fallback_download_is_logged_as_html(self) -> None:
        harness = build_harness(
            overrides={"document_renderer": FailingDocumentRenderer()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        harness.service.export(record.summary_id, S.OWNER_USER_ID)

        event = harness.downloads.for_summary(record.summary_id)[0]
        assert event.format == "html"
        assert event.pdf_available is False

    def test_the_download_is_timestamped_from_the_clock(self) -> None:
        from uc09_summary.adapters.mock.clock import DEFAULT_NOW

        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert harness.downloads.for_summary(record.summary_id)[0].downloaded_at == (
            DEFAULT_NOW
        )

    def test_the_http_download_is_logged(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/pdf",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert len(harness.downloads.for_summary(record.summary_id)) == 1

    def test_a_preview_is_not_a_download(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/preview",
            headers=harness.as_user(S.OWNER_USER_ID),
        )
        harness.service.preview_html(record.summary_id, S.OWNER_USER_ID)

        assert harness.downloads.for_summary(record.summary_id) == ()

    def test_the_download_log_is_exposed_to_the_owner(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/pdf",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/downloads",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["session_id"] == record.session_id

    def test_a_failed_download_attempt_by_a_stranger_logs_nothing(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/pdf",
            headers=harness.as_user(S.OTHER_USER_ID),
        )

        assert harness.downloads.for_summary(record.summary_id) == ()


class TestSessionStatus:
    def test_the_record_carries_summary_generated(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert record.session_status is SessionStatus.SUMMARY_GENERATED

    def test_it_is_set_even_when_the_generator_fell_back(self) -> None:
        from uc09_summary.adapters.mock.generator import UnavailableGenerator

        harness = build_harness(overrides={"summary_generator": UnavailableGenerator()})
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert record.session_status is SessionStatus.SUMMARY_GENERATED

    def test_it_is_set_on_a_partial_summary_too(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)

        assert record.session_status is SessionStatus.SUMMARY_GENERATED
        assert record.is_partial is True

    def test_the_status_is_serialised_lowercase(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert response.json()["session_status"] == "summary_generated"

    def test_the_upstream_session_is_left_untouched(self) -> None:
        """This component records the transition; it does not write upstream."""
        harness = build_harness()
        before = S.SESSIONS[S.SESSION_COMPLETE].status
        harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert S.SESSIONS[S.SESSION_COMPLETE].status == before


class TestRegeneration:
    def test_regenerating_creates_a_new_record(self) -> None:
        harness = build_harness()
        first = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        second = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert first.summary_id != second.summary_id
        assert len(harness.summaries.for_session(S.SESSION_COMPLETE)) == 2

    def test_the_earlier_record_remains_retrievable(self) -> None:
        harness = build_harness()
        first = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert harness.service.get(first.summary_id, S.OWNER_USER_ID) == first

    def test_both_records_describe_the_same_session_identically(self) -> None:
        harness = build_harness()
        first = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        second = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert first.topics_covered == second.topics_covered
        assert first.key_concepts == second.key_concepts
        assert first.resources_referenced == second.resources_referenced
