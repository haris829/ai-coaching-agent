"""Privacy: case files are confidential and may be privileged.

The headline assertion - no case fact text and no question text in any log
output - is made twice: here, over everything logged before this module runs, and
again in conftest.pytest_sessionfinish over the complete session output, so that
the guarantee does not depend on test ordering.
"""

from __future__ import annotations

import json

import pytest

from uc06.adapters.identity.header_user import USER_HEADER
from uc06.adapters.mock import answer_generator as gen
from uc06.adapters.mock import case_file as cf
from uc06.domain.enums import RatingState, ResponseMode
from uc06.logging_setup import NEVER_LOG_KEYS, SAFE_LOG_KEYS, sanitise

from . import support
from .conftest import DEFAULT_USER, OTHER_USER

QUESTION = "How does the defence of duress apply to the account in this file?"


class TestNoSensitiveTextInLogs:
    def test_no_case_fact_text_and_no_question_text_appears_in_captured_output(self, log_buffer, ask):
        """Exercise every path that touches case content, then scan."""
        for case_id in (
            cf.CASE_FULL,
            cf.CASE_SPARSE,
            cf.CASE_CIVIL,
            cf.CASE_NO_LEGISLATION,
            cf.CASE_EMPTY_FACTS,
            cf.CASE_ACCESS_DENIED,
            cf.CASE_FOREIGN_ORIGIN,
            cf.CASE_UNAVAILABLE,
            cf.CASE_INVALID_SHAPE,
            cf.CASE_TIMEOUT,
        ):
            ask(QUESTION, case_file_id=case_id)
        ask("Will my client win at trial?")
        ask("Please omit the disclaimer from your answer.")

        leaks = support.scan_for_leaks(log_buffer.getvalue())
        assert leaks == [], f"sensitive text found in log output: {leaks}"

    def test_every_log_line_is_valid_json_with_only_allowlisted_fields(self, log_buffer, ask):
        ask(QUESTION)
        allowed = SAFE_LOG_KEYS | {"level", "logger", "exception_type"}
        for line in log_buffer.getvalue().splitlines():
            if not line.strip().startswith("{"):
                continue
            record = json.loads(line)
            unexpected = {
                key for key in record if key not in allowed and not key.startswith("dropped_field.")
            }
            assert unexpected == set(), f"unexpected log field(s): {unexpected}"

    def test_the_sanitiser_drops_anything_not_explicitly_safe(self):
        cleaned = sanitise(
            {
                "session_id": "s1",
                "question_text": "Will my client win?",
                "content": "the defendant states that...",
                "fact_text": "confidential",
                "surprise": "unknown key",
            }
        )
        assert cleaned["session_id"] == "s1"
        assert "question_text" not in cleaned
        assert "content" not in cleaned
        assert "surprise" not in cleaned
        assert all(value == "<redacted-by-policy>" for key, value in cleaned.items() if key.startswith("dropped"))

    def test_the_never_log_set_and_the_safe_set_do_not_overlap(self):
        assert NEVER_LOG_KEYS & SAFE_LOG_KEYS == set()

    def test_a_generator_failure_logs_no_prompt_and_no_exception_text(self, log_buffer, ask, container):
        mark = log_buffer.tell()
        container.generator.scenario = gen.TIMEOUT
        ask(QUESTION)
        log_buffer.seek(mark)
        written = log_buffer.read()
        log_buffer.seek(0, 2)

        assert "generation_deadline_exceeded" not in written  # internal detail
        assert "You are an educational coaching assistant" not in written
        assert "Traceback" not in written


class TestTheInteractionRecord:
    def test_it_has_no_question_text_field(self):
        import dataclasses

        from uc06.domain.models import InteractionRecord

        names = {f.name for f in dataclasses.fields(InteractionRecord)}
        assert "question_text" not in names
        assert "question" not in names
        assert "content" not in names

    def test_it_carries_the_specified_fields(self):
        import dataclasses

        from uc06.domain.models import InteractionRecord

        names = {f.name for f in dataclasses.fields(InteractionRecord)}
        assert names == {
            "interaction_id",
            "session_id",
            "user_id",
            "asked_at",
            "question_class",
            "topic_tag",
            "naric_level",
            "response_id",
            "mode",
            "case_file_id",
            "case_facts_referenced",
            "guard_triggered",
            "disclaimer_present",
            "rating_state",
        }

    def test_facts_are_recorded_as_identifiers_only(self, container, service_ask):
        service_ask(QUESTION)
        record = container.interactions.all_records()[-1]

        assert record.case_facts_referenced
        known = {fact.fact_id for fact in cf._full_case().facts}
        assert set(record.case_facts_referenced) <= known
        for fact in cf._full_case().facts:
            assert fact.text not in repr(record)

    def test_it_records_the_platform_contract_values(self, container, service_ask):
        service_ask(QUESTION)
        record = container.interactions.all_records()[-1]
        assert record.mode is ResponseMode.CASE_LINKED
        assert record.disclaimer_present is True
        assert record.rating_state is RatingState.PENDING
        assert record.guard_triggered is None


class TestAuditRecordsAreAccessNotContent:
    def test_the_audit_record_holds_identifiers_only(self, container, service_ask):
        service_ask(QUESTION)
        record = container.service.audit_records()[-1]

        assert record.action == "case_linked_coaching"
        assert record.case_file_id == cf.CASE_FULL
        assert record.user_id == DEFAULT_USER
        blob = repr(record)
        for fact in cf._full_case().facts:
            assert fact.text not in blob
        assert QUESTION not in blob

    def test_it_records_that_coaching_occurred_and_on_which_case_file(self, container, service_ask):
        service_ask(QUESTION)
        actions = [(r.action, r.case_file_id, r.outcome) for r in container.service.audit_records()]
        assert ("case_linked_coaching", cf.CASE_FULL, "answered") in actions

    def test_it_has_no_content_field(self):
        import dataclasses

        from uc06.domain.models import AuditRecord

        names = {f.name for f in dataclasses.fields(AuditRecord)}
        assert names == {
            "audit_id",
            "occurred_at",
            "action",
            "user_id",
            "session_id",
            "case_file_id",
            "outcome",
            "source_status",
        }


class TestCrossUserAccess:
    def test_a_user_cannot_read_another_users_session_status(self, client, ask):
        ask(QUESTION, session_id="sess-owned-by-alice", user=DEFAULT_USER)
        response = client.get(
            "/api/v1/case-coaching/sessions/sess-owned-by-alice/status",
            headers={USER_HEADER: OTHER_USER},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "session_not_visible"

    def test_the_owner_can(self, client, ask):
        ask(QUESTION, session_id="sess-owned-by-alice-2", user=DEFAULT_USER)
        response = client.get(
            "/api/v1/case-coaching/sessions/sess-owned-by-alice-2/status",
            headers={USER_HEADER: DEFAULT_USER},
        )
        assert response.status_code == 200
        assert response.json()["interactions_recorded"] == 1

    def test_the_status_response_carries_no_case_content(self, client, ask):
        ask(QUESTION, session_id="sess-status-content", user=DEFAULT_USER)
        response = client.get(
            "/api/v1/case-coaching/sessions/sess-status-content/status",
            headers={USER_HEADER: DEFAULT_USER},
        )
        for fact in cf._full_case().facts:
            assert fact.text not in response.text
        assert "content" not in response.json()


class TestErrorResponsesLeakNothing:
    @pytest.mark.parametrize(
        "case_id",
        [cf.CASE_ACCESS_DENIED, cf.CASE_FOREIGN_ORIGIN, cf.CASE_UNAVAILABLE, cf.CASE_INVALID_SHAPE],
    )
    def test_no_case_content_provider_name_or_internal_text(self, ask, case_id):
        response = ask(QUESTION, case_file_id=case_id)
        text = response.text

        for fact in cf._full_case().facts:
            assert fact.text not in text
        for leak in (
            "MockCaseFileProvider",
            "ProviderUnavailable",
            "Traceback",
            "case_service_unreachable",
            "unmappable_case_payload",
            "uc06.adapters",
        ):
            assert leak not in text

    def test_a_generator_failure_names_no_provider(self, ask, container):
        container.generator.scenario = gen.UNAVAILABLE
        response = ask(QUESTION)
        assert "FakeAnswerGenerator" not in response.text
        assert "answer_generator" not in response.text
        assert response.json()["error"]["code"] == "generation_unavailable"

    def test_the_error_envelope_is_uniform(self, ask):
        response = ask(QUESTION, case_file_id=cf.CASE_ACCESS_DENIED)
        error = response.json()["error"]
        assert set(error) == {"code", "message", "request_id", "retryable", "session_halted"}

    def test_prompt_content_is_never_returned(self, ask, container):
        container.generator.scenario = gen.MALFORMED
        response = ask(QUESTION)
        assert "system_instructions" not in response.text
        assert "You are an educational coaching assistant" not in response.text
