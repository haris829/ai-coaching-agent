"""Resilience: degraded paths still answer, and still carry the disclaimer."""

from __future__ import annotations

import time

import pytest

from uc06.adapters.mock import answer_generator as gen
from uc06.adapters.mock import case_file as cf
from uc06.domain.disclaimer import CANONICAL_DISCLAIMER
from uc06.domain.enums import NaricLevelSource, ResponseMode, SourceStatus

QUESTION = "How does the defence of duress apply to the account in this file?"


class TestLearnerContextUnavailable:
    @pytest.mark.parametrize("session_id", ["sess-ctx-unavailable", "sess-ctx-timeout"])
    def test_the_question_is_still_answered(self, ask, session_id):
        response = ask(QUESTION, session_id=session_id)
        assert response.status_code == 200
        assert len(response.json()["content"].split()) > 60

    @pytest.mark.parametrize("session_id", ["sess-ctx-unavailable", "sess-ctx-timeout"])
    def test_it_defaults_to_level_5_marked_default(self, ask, session_id):
        body = ask(QUESTION, session_id=session_id).json()
        assert body["naric_level"] == "LEVEL_5"
        assert body["naric_level_source"] == NaricLevelSource.DEFAULT.value
        assert body["explanation_profile"] == "intermediate"

    @pytest.mark.parametrize("session_id", ["sess-ctx-unavailable", "sess-ctx-timeout"])
    def test_the_status_is_recorded_as_unavailable_not_empty(self, ask, session_id):
        body = ask(QUESTION, session_id=session_id).json()
        assert body["learner_context_status"] == SourceStatus.UNAVAILABLE.value

    @pytest.mark.parametrize("session_id", ["sess-ctx-unavailable", "sess-ctx-timeout"])
    def test_the_disclaimer_is_intact(self, ask, session_id):
        assert ask(QUESTION, session_id=session_id).json()["disclaimer"] == CANONICAL_DISCLAIMER

    def test_a_context_failure_never_removes_the_guard(self, ask):
        body = ask("Will my client win at trial?", session_id="sess-ctx-unavailable").json()
        assert body["guard_triggered"] == "outcome_prediction"
        assert body["disclaimer"] == CANONICAL_DISCLAIMER

    def test_an_unmappable_naric_value_is_invalid_not_a_level(self, ask):
        """A value mapping to no enum member is an invalid response, never
        rounded to a neighbour."""
        body = ask(QUESTION, session_id="sess-ctx-badlevel").json()
        assert body["learner_context_status"] == SourceStatus.INVALID.value
        assert body["naric_level"] == "LEVEL_5"
        assert body["naric_level_source"] == NaricLevelSource.DEFAULT.value

    def test_an_unconfirmed_session_does_not_get_case_content(self, ask):
        """We cannot confirm the session is in case-linked mode, so we degrade
        rather than read a confidential file on an unverified session."""
        body = ask(QUESTION, session_id="sess-ctx-unavailable").json()
        assert body["mode"] == ResponseMode.GENERAL_FALLBACK.value
        assert body["case_facts_referenced"] == []
        assert body["notice"]
        for fact in cf._full_case().facts:
            assert fact.text not in body["content"]


class TestGeneratorFailures:
    def test_a_timeout_returns_a_retryable_error(self, ask, container):
        container.generator.scenario = gen.TIMEOUT
        response = ask(QUESTION)

        assert response.status_code == 504
        body = response.json()
        assert body["error"]["code"] == "generation_timeout"
        assert body["error"]["retryable"] is True
        assert body["disclaimer"] == CANONICAL_DISCLAIMER

    def test_the_timeout_error_returns_within_the_configured_budget(self, ask, container):
        container.generator.scenario = gen.TIMEOUT
        started = time.monotonic()
        ask(QUESTION)
        elapsed_ms = (time.monotonic() - started) * 1000
        assert elapsed_ms < container.settings.generation_timeout_ms

    def test_the_configured_timeout_reaches_the_generator(self, container, service_ask):
        service_ask(QUESTION)
        assert container.generator.calls[-1].timeout_ms == container.settings.generation_timeout_ms

    def test_an_unavailable_generator_returns_a_retryable_error(self, ask, container):
        container.generator.scenario = gen.UNAVAILABLE
        response = ask(QUESTION)
        assert response.status_code == 503
        assert response.json()["error"]["retryable"] is True

    @pytest.mark.parametrize("scenario", [gen.MALFORMED, gen.MISSING_FIELD, gen.FABRICATED_FACT])
    def test_unusable_output_is_a_non_retryable_invalid_response(self, ask, container, scenario):
        container.generator.scenario = scenario
        response = ask(QUESTION)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "generation_invalid"
        assert response.json()["error"]["retryable"] is False

    @pytest.mark.parametrize(
        "scenario", [gen.TIMEOUT, gen.UNAVAILABLE, gen.MALFORMED, gen.MISSING_FIELD, gen.FABRICATED_FACT]
    )
    def test_every_generator_failure_still_carries_the_disclaimer(self, ask, container, scenario):
        container.generator.scenario = scenario
        assert ask(QUESTION).json()["disclaimer"] == CANONICAL_DISCLAIMER


class TestDoubleDegradation:
    def test_an_unreadable_case_file_and_a_dead_generator_still_answer(self, ask, container):
        """Degrading twice is still an answer: the in-domain library always
        produces a substantive general explanation."""
        container.generator.scenario = gen.UNAVAILABLE
        response = ask(QUESTION, case_file_id=cf.CASE_UNAVAILABLE)
        body = response.json()

        assert response.status_code == 200
        assert body["mode"] == ResponseMode.GENERAL_FALLBACK.value
        assert len(body["content"].split()) > 60
        assert body["notice"]
        assert body["disclaimer"] == CANONICAL_DISCLAIMER

    def test_it_still_carries_no_case_facts(self, ask, container):
        container.generator.scenario = gen.UNAVAILABLE
        body = ask(QUESTION, case_file_id=cf.CASE_UNAVAILABLE).json()
        assert body["case_facts_referenced"] == []
        assert "[[fact:" not in body["content"]

    def test_a_generator_fabricating_on_the_fallback_path_is_discarded(self, ask, container):
        """No case file is loaded there, so no marker could ever resolve."""
        container.generator.scenario = gen.FABRICATED_FACT
        body = ask(QUESTION, case_file_id=cf.CASE_UNAVAILABLE).json()
        assert "[[fact:" not in body["content"]
        assert gen.GHOST_FACT_ID not in body["content"]
        assert body["disclaimer"] == CANONICAL_DISCLAIMER

    def test_a_prediction_on_the_fallback_path_is_replaced(self, ask, container):
        container.generator.scenario = gen.OUTCOME_PREDICTION
        body = ask(QUESTION, case_file_id=cf.CASE_UNAVAILABLE).json()
        assert "will win at trial" not in body["content"].lower()
        assert len(body["content"].split()) > 60

    def test_context_and_case_file_both_unavailable(self, ask):
        body = ask(QUESTION, case_file_id=cf.CASE_UNAVAILABLE, session_id="sess-ctx-unavailable").json()
        assert body["naric_level"] == "LEVEL_5"
        assert body["naric_level_source"] == NaricLevelSource.DEFAULT.value
        assert body["mode"] == ResponseMode.GENERAL_FALLBACK.value
        assert body["disclaimer"] == CANONICAL_DISCLAIMER


class TestEveryDegradedPathIsExhaustivelyChecked:
    """The disclaimer assertion, repeated over the full cross-product of failure
    states. tests/test_output_scan.py checks the raw body; this checks the
    field and the mode together."""

    @pytest.mark.parametrize(
        "case_id",
        [
            cf.CASE_FULL,
            cf.CASE_SPARSE,
            cf.CASE_EMPTY_FACTS,
            cf.CASE_NO_LEGISLATION,
            cf.CASE_UNAVAILABLE,
            cf.CASE_TIMEOUT,
            cf.CASE_INVALID_SHAPE,
            cf.CASE_ACCESS_DENIED,
            cf.CASE_FOREIGN_ORIGIN,
        ],
    )
    @pytest.mark.parametrize(
        "session_id",
        ["sess-level-3", "sess-level-7", "sess-ctx-unavailable", "sess-ctx-badlevel", "sess-no-practice-area"],
    )
    @pytest.mark.parametrize("scenario", [gen.WELL_FORMED, gen.TIMEOUT, gen.MALFORMED])
    def test_the_disclaimer_is_present_in_every_combination(self, ask, container, case_id, session_id, scenario):
        container.generator.scenario = scenario
        response = ask(QUESTION, case_file_id=case_id, session_id=session_id)
        assert response.json()["disclaimer"] == CANONICAL_DISCLAIMER
        assert response.text.count(CANONICAL_DISCLAIMER) == 1
