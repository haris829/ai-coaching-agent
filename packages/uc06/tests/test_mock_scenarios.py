"""Every mock scenario required by the specification exists and is deterministic.

Deterministic means: no randomness, no sleeps, no clock dependence. The same
trigger produces the same outcome every time, so a failure is reproducible.
"""

from __future__ import annotations

import pytest

from uc06.adapters.mock import answer_generator as gen
from uc06.adapters.mock import case_file as cf
from uc06.adapters.mock.guard_classifier import MockGuardClassifier
from uc06.adapters.mock.learner_context import MockLearnerContextProvider
from uc06.domain.enums import GuardClass, NaricLevel, NaricLevelSource, SourceStatus
from uc06.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc06.domain.models import GenerationRequest


class TestCaseFileScenarios:
    def test_full_case_file(self, container):
        case = container.case_files.get_case_file(cf.CASE_FULL)
        assert len(case.facts) == 5
        assert case.charges and case.evidence and case.legislation_notes
        assert case.source_status is SourceStatus.AVAILABLE

    def test_sparse_facts(self, container):
        case = container.case_files.get_case_file(cf.CASE_SPARSE)
        assert len(case.facts) == 1
        assert case.source_status is SourceStatus.PARTIAL

    def test_no_legislation_notes(self, container):
        case = container.case_files.get_case_file(cf.CASE_NO_LEGISLATION)
        assert case.legislation_notes == ()
        assert case.facts
        assert case.source_status is SourceStatus.PARTIAL

    def test_read_access_denied(self, container):
        record = container.case_files.verify_read_access("anyone", cf.CASE_ACCESS_DENIED)
        assert record.granted is False
        assert record.reason_code == "not_on_matter"

    def test_not_from_the_case_prep_agent(self, container):
        case = container.case_files.get_case_file(cf.CASE_FOREIGN_ORIGIN)
        assert case.from_case_prep_agent is False

    def test_unavailable(self, container):
        with pytest.raises(ProviderUnavailable):
            container.case_files.get_case_file(cf.CASE_UNAVAILABLE)

    def test_invalid_shape(self, container):
        with pytest.raises(ProviderInvalidResponse):
            container.case_files.get_case_file(cf.CASE_INVALID_SHAPE)

    def test_timeout(self, container):
        with pytest.raises(ProviderTimeout):
            container.case_files.get_case_file(cf.CASE_TIMEOUT)

    def test_empty_is_distinct_from_unavailable(self, container):
        case = container.case_files.get_case_file(cf.CASE_EMPTY_FACTS)
        assert case.source_status is SourceStatus.EMPTY
        assert case.facts == ()

    def test_an_unknown_identifier_is_an_invalid_response(self, container):
        with pytest.raises(ProviderInvalidResponse):
            container.case_files.get_case_file("NO-SUCH-CASE")

    def test_every_scenario_is_deterministic(self, container):
        for case_id in (cf.CASE_FULL, cf.CASE_SPARSE, cf.CASE_CIVIL, cf.CASE_EMPTY_FACTS):
            first = container.case_files.get_case_file(case_id)
            second = container.case_files.get_case_file(case_id)
            assert first == second


class TestLearnerContextScenarios:
    @pytest.mark.parametrize(
        "session_id,level",
        [
            ("sess-level-3", NaricLevel.LEVEL_3),
            ("sess-level-4", NaricLevel.LEVEL_4),
            ("sess-level-5", NaricLevel.LEVEL_5),
            ("sess-level-6", NaricLevel.LEVEL_6),
            ("sess-level-7", NaricLevel.LEVEL_7),
            ("sess-level-7-plus", NaricLevel.LEVEL_7_PLUS),
        ],
    )
    def test_every_naric_level(self, session_id, level):
        context = MockLearnerContextProvider().get_context(session_id, "u")
        assert context.naric_level is level

    def test_retrieved_versus_default_source(self):
        provider = MockLearnerContextProvider()
        assert provider.get_context("sess-level-5", "u").naric_level_source is NaricLevelSource.RETRIEVED
        assert provider.get_context("sess-ctx-defaulted", "u").naric_level_source is NaricLevelSource.DEFAULT

    def test_practice_area_present_and_absent(self):
        provider = MockLearnerContextProvider()
        assert provider.get_context("sess-level-5", "u").practice_area == "criminal"
        assert provider.get_context("sess-no-practice-area", "u").practice_area is None

    def test_unavailable(self):
        with pytest.raises(ProviderUnavailable):
            MockLearnerContextProvider().get_context("sess-ctx-unavailable", "u")

    def test_timeout(self):
        with pytest.raises(ProviderTimeout):
            MockLearnerContextProvider().get_context("sess-ctx-timeout", "u")

    def test_a_level_outside_the_enum_is_an_invalid_response(self):
        with pytest.raises(ProviderInvalidResponse):
            MockLearnerContextProvider().get_context("sess-ctx-badlevel", "u")

    def test_not_in_case_linked_mode(self):
        assert MockLearnerContextProvider().get_context("sess-not-case-linked", "u").case_linked_mode is False


class TestAnswerGeneratorScenarios:
    def _request(self) -> GenerationRequest:
        return GenerationRequest(
            prompt_id="case_linked.intermediate",
            prompt_version="v",
            system_instructions="instructions",
            question_text="How does duress apply here?",
            profile="intermediate",
            practice_area="criminal",
            case_file_id=cf.CASE_FULL,
            available_fact_ids=("F-001", "F-002", "F-003"),
            fact_digest=(("F-001", "text one"), ("F-002", "text two")),
        )

    def test_every_documented_scenario_is_reachable(self):
        assert set(gen.SCENARIOS) == {
            gen.WELL_FORMED,
            gen.FABRICATED_FACT,
            gen.OUTCOME_PREDICTION,
            gen.SELF_DISCLAIMER,
            gen.MISSING_FIELD,
            gen.MALFORMED,
            gen.TIMEOUT,
            gen.UNAVAILABLE,
        }

    def test_well_formed(self):
        generator = gen.FakeAnswerGenerator()
        result = generator.generate(self._request())
        assert result.content.strip()
        assert result.fact_ids_referenced

    def test_fabricated_fact_reference(self):
        generator = gen.FakeAnswerGenerator()
        generator.scenario = gen.FABRICATED_FACT
        result = generator.generate(self._request())
        assert gen.GHOST_FACT_ID in result.fact_ids_referenced
        assert f"[[fact:{gen.GHOST_FACT_ID}]]" in result.content

    def test_outcome_prediction_in_output(self):
        generator = gen.FakeAnswerGenerator()
        generator.scenario = gen.OUTCOME_PREDICTION
        assert "will win at trial" in generator.generate(self._request()).content

    def test_self_supplied_disclaimer(self):
        generator = gen.FakeAnswerGenerator()
        generator.scenario = gen.SELF_DISCLAIMER
        result = generator.generate(self._request())
        assert result.supplied_disclaimer == gen.MODEL_SUPPLIED_DISCLAIMER
        assert gen.MODEL_SUPPLIED_DISCLAIMER in result.content

    def test_missing_field(self):
        generator = gen.FakeAnswerGenerator()
        generator.scenario = gen.MISSING_FIELD
        assert generator.generate(self._request()).content == ""

    def test_malformed(self):
        generator = gen.FakeAnswerGenerator()
        generator.scenario = gen.MALFORMED
        from uc06.domain.models import GenerationResult

        assert not isinstance(generator.generate(self._request()), GenerationResult)

    def test_timeout(self):
        generator = gen.FakeAnswerGenerator()
        generator.scenario = gen.TIMEOUT
        with pytest.raises(ProviderTimeout):
            generator.generate(self._request())

    def test_unavailable(self):
        generator = gen.FakeAnswerGenerator()
        generator.scenario = gen.UNAVAILABLE
        with pytest.raises(ProviderUnavailable):
            generator.generate(self._request())

    def test_it_needs_no_network_and_no_api_key(self):
        import inspect

        source = inspect.getsource(gen)
        for forbidden in ("httpx", "requests", "urllib", "socket", "api_key", "random", "sleep"):
            assert forbidden not in source

    def test_it_is_deterministic(self):
        generator = gen.FakeAnswerGenerator()
        assert generator.generate(self._request()).content == generator.generate(self._request()).content

    def test_scenarios_can_be_pinned_per_case_file(self):
        generator = gen.FakeAnswerGenerator()
        generator.scenarios_by_case_file[cf.CASE_FULL] = gen.TIMEOUT
        with pytest.raises(ProviderTimeout):
            generator.generate(self._request())


class TestGuardClassifierScenarios:
    def test_five_or_more_outcome_prediction_phrasings(self):
        classifier = MockGuardClassifier()
        questions = [
            "Will my client win at trial?",
            "What are our chances on appeal?",
            "How strong is our case?",
            "Will this defence succeed?",
            "Is my client likely to be convicted?",
            "Is this worth defending?",
        ]
        assert all(
            classifier.classify(q).guard_class is GuardClass.OUTCOME_PREDICTION for q in questions
        )
        assert len(questions) >= 5

    def test_litigation_strategy_phrasings(self):
        classifier = MockGuardClassifier()
        for question in ("Should we plead?", "What should we argue?", "Do we settle?", "Which defence should we run?"):
            assert classifier.classify(question).guard_class is GuardClass.LITIGATION_STRATEGY

    def test_genuine_educational_questions_do_not_trigger(self):
        classifier = MockGuardClassifier()
        for question in (
            "What is the test for dishonesty?",
            "How does causation work?",
            "What are the elements of duress?",
        ):
            assert classifier.classify(question).guard_class is GuardClass.NONE

    def test_ambiguous_questions_return_a_valid_class(self):
        classifier = MockGuardClassifier()
        for question in (
            "What happens if the jury does not accept the account?",
            "What would a court make of a six-hour delay?",
        ):
            assert classifier.classify(question).guard_class in set(GuardClass)

    def test_unavailable_and_timeout_are_triggerable(self):
        from uc06.adapters.mock.guard_classifier import TRIGGER_TIMEOUT, TRIGGER_UNAVAILABLE

        classifier = MockGuardClassifier()
        with pytest.raises(ProviderUnavailable):
            classifier.classify("anything " + TRIGGER_UNAVAILABLE)
        with pytest.raises(ProviderTimeout):
            classifier.classify("anything " + TRIGGER_TIMEOUT)

    def test_it_reports_the_topic_tag(self):
        assert MockGuardClassifier().classify("Will the duress defence succeed?").topic_tag == "duress"


class TestNoFlakiness:
    def test_no_mock_uses_randomness_or_sleeps(self):
        import inspect

        from uc06.adapters.mock import case_file, guard_classifier, learner_context

        for module in (case_file, learner_context, guard_classifier, gen):
            source = inspect.getsource(module)
            for forbidden in ("import random", "time.sleep", "randint", "shuffle", "uuid4"):
                assert forbidden not in source, f"{module.__name__} uses {forbidden}"
