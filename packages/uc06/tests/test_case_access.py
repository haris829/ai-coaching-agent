"""Case file access: verified before content loads, on every request, never cached."""

from __future__ import annotations

import pytest

from uc06.adapters.identity.header_user import USER_HEADER
from uc06.adapters.mock import case_file as cf
from uc06.domain.disclaimer import CANONICAL_DISCLAIMER
from uc06.domain.enums import ResponseMode, SecurityIncidentKind, SourceStatus

from .conftest import DEFAULT_USER

QUESTION = "How does the defence of duress apply to the account in this file?"


class TestAccessIsVerifiedFirstAndEveryTime:
    def test_access_is_checked_before_any_content_is_loaded(self, container, service_ask):
        service_ask(QUESTION)
        assert container.case_files.access_checks, "access was never checked"
        assert container.case_files.reads, "case file was never read"
        # The mock records the order in which the port methods were called.
        assert len(container.case_files.access_checks) == 1
        assert len(container.case_files.reads) == 1

    def test_no_content_is_loaded_when_access_is_denied(self, container, service_ask):
        outcome = service_ask(QUESTION, case_file_id=cf.CASE_ACCESS_DENIED)
        assert container.case_files.access_checks == [(DEFAULT_USER, cf.CASE_ACCESS_DENIED)]
        assert container.case_files.reads == [], "case content was loaded despite denial"
        assert outcome.status_code == 403

    def test_access_is_re_verified_on_every_request_never_cached(self, container, service_ask):
        for _ in range(4):
            service_ask(QUESTION, session_id="sess-level-5")
        assert len(container.case_files.access_checks) == 4

    def test_a_revoked_permission_takes_effect_on_the_next_request(self, container, service_ask):
        """Nothing carries an earlier decision forward: the second question in
        the same session is refused once the user is off the matter."""
        first = service_ask(QUESTION, user_id=DEFAULT_USER)
        assert first.status_code == 200

        second = service_ask(QUESTION, user_id=cf.OTHER_USER)
        assert second.status_code == 403


class TestDeniedAccess:
    def test_denied_returns_a_distinct_code(self, ask):
        response = ask(QUESTION, case_file_id=cf.CASE_ACCESS_DENIED)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "case_access_denied"
        assert response.json()["disclaimer"] == CANONICAL_DISCLAIMER

    def test_denied_is_recorded_as_a_security_incident(self, container, service_ask):
        service_ask(QUESTION, case_file_id=cf.CASE_ACCESS_DENIED)
        kinds = [i.kind for i in container.security_incidents.incidents()]
        assert SecurityIncidentKind.UNAUTHORISED_CASE_ACCESS in kinds

    def test_denied_is_audited(self, container, service_ask):
        service_ask(QUESTION, case_file_id=cf.CASE_ACCESS_DENIED)
        outcomes = [record.outcome for record in container.service.audit_records()]
        assert "access_denied" in outcomes

    def test_a_different_user_on_the_same_case_file_is_denied(self, ask):
        response = ask(QUESTION, user=cf.OTHER_USER)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "case_access_denied"

    def test_the_denial_leaks_no_case_content(self, ask):
        response = ask(QUESTION, case_file_id=cf.CASE_ACCESS_DENIED)
        for fact in cf._full_case().facts:
            assert fact.text not in response.text


class TestOriginVerification:
    def test_a_case_file_not_from_the_case_prep_agent_is_refused(self, ask):
        response = ask(QUESTION, case_file_id=cf.CASE_FOREIGN_ORIGIN)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "case_origin_rejected"

    def test_the_refusal_carries_no_case_content(self, ask):
        response = ask(QUESTION, case_file_id=cf.CASE_FOREIGN_ORIGIN)
        for fact in cf._foreign_origin_case().facts:
            assert fact.text not in response.text

    def test_origin_is_checked_before_the_content_is_used(self, container, service_ask):
        outcome = service_ask(QUESTION, case_file_id=cf.CASE_FOREIGN_ORIGIN)
        assert outcome.status_code == 409
        # The generator was never asked to explain anything about it.
        assert container.generator.calls == []

    def test_the_refusal_is_audited(self, container, service_ask):
        service_ask(QUESTION, case_file_id=cf.CASE_FOREIGN_ORIGIN)
        assert "origin_rejected" in [r.outcome for r in container.service.audit_records()]


class TestReadFailureDegradesToGeneralCoaching:
    @pytest.mark.parametrize(
        "case_id,expected_status",
        [
            (cf.CASE_UNAVAILABLE, SourceStatus.UNAVAILABLE),
            (cf.CASE_TIMEOUT, SourceStatus.UNAVAILABLE),
            (cf.CASE_INVALID_SHAPE, SourceStatus.INVALID),
        ],
    )
    def test_the_learner_is_never_left_with_nothing(self, ask, case_id, expected_status):
        response = ask(QUESTION, case_file_id=case_id)
        body = response.json()

        assert response.status_code == 200
        assert body["mode"] == ResponseMode.GENERAL_FALLBACK.value
        assert body["case_file_status"] == expected_status.value
        assert body["notice"], "the learner must be told the case file could not be accessed"
        assert len(body["content"].split()) > 60, "the fallback must still be substantive"
        assert body["disclaimer"] == CANONICAL_DISCLAIMER

    @pytest.mark.parametrize("case_id", [cf.CASE_UNAVAILABLE, cf.CASE_TIMEOUT, cf.CASE_INVALID_SHAPE])
    def test_the_fallback_carries_no_case_facts(self, ask, case_id):
        body = ask(QUESTION, case_file_id=case_id).json()
        assert body["case_facts_referenced"] == []
        for fact in cf._full_case().facts:
            assert fact.text not in body["content"]
        assert "[[fact:" not in body["content"]

    def test_the_fallback_is_recorded_as_not_case_linked(self, container, service_ask):
        service_ask(QUESTION, case_file_id=cf.CASE_UNAVAILABLE)
        record = container.interactions.all_records()[-1]
        assert record.mode is ResponseMode.GENERAL_FALLBACK
        assert record.case_facts_referenced == ()

    def test_an_unreadable_case_file_is_not_treated_as_empty(self, ask):
        """`empty` and `unavailable` are different states and are never conflated."""
        unavailable = ask(QUESTION, case_file_id=cf.CASE_UNAVAILABLE).json()
        empty = ask(QUESTION, case_file_id=cf.CASE_EMPTY_FACTS).json()

        assert unavailable["case_file_status"] == SourceStatus.UNAVAILABLE.value
        assert unavailable["mode"] == ResponseMode.GENERAL_FALLBACK.value

        # An empty case file was read successfully: it stays case-linked.
        assert empty["case_file_status"] == SourceStatus.EMPTY.value
        assert empty["mode"] == ResponseMode.CASE_LINKED.value

    def test_a_partial_case_file_is_marked_partial_and_still_case_linked(self, ask):
        body = ask(QUESTION, case_file_id=cf.CASE_NO_LEGISLATION).json()
        assert body["case_file_status"] == SourceStatus.PARTIAL.value
        assert body["mode"] == ResponseMode.CASE_LINKED.value


class TestWhatIsRead:
    def test_charges_facts_evidence_and_legislation_are_all_available(self, container):
        case = container.case_files.get_case_file(cf.CASE_FULL)
        assert case.charges and case.facts and case.evidence and case.legislation_notes

    def test_every_fact_carries_a_stable_identifier(self, container):
        for case_id in (cf.CASE_FULL, cf.CASE_SPARSE, cf.CASE_CIVIL):
            case = container.case_files.get_case_file(case_id)
            ids = [fact.fact_id for fact in case.facts]
            assert all(ids), "a fact without an identifier cannot be verified or logged"
            assert len(ids) == len(set(ids)), "fact identifiers must be unique"

    def test_charges_and_legislation_reach_the_generator(self, container, service_ask):
        service_ask(QUESTION)
        request = container.generator.calls[-1]
        assert request.charges
        assert request.legislation
        assert request.available_fact_ids
