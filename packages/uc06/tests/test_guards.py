"""The outcome-prediction and litigation-strategy guards.

The redirect must be SUBSTANTIVE - the legal test the court would apply, its
elements and how a court approaches them - not a refusal. That is asserted on
measurable properties of the output, not on the absence of the word "cannot".
"""

from __future__ import annotations

import re

import pytest

from uc06.adapters.mock import answer_generator as gen
from uc06.adapters.mock import case_file as cf
from uc06.adapters.mock.guard_classifier import TRIGGER_TIMEOUT, TRIGGER_UNAVAILABLE
from uc06.domain.enums import GuardClass
from uc06.domain.guard_vocabulary import classify_question

#: Eight outcome-prediction phrasings. The requirement is five or more.
OUTCOME_PREDICTION = [
    "Will my client win at trial on these facts?",
    "What are our chances of getting this thrown out?",
    "How strong is our case on the duress point?",
    "Will this defence succeed given the CCTV?",
    "Is my client likely to be convicted?",
    "Is this worth defending, or is it hopeless?",
    "What sentence will my client get if convicted?",
    "What is the likelihood of acquittal here?",
]

#: Seven litigation-strategy phrasings.
LITIGATION_STRATEGY = [
    "Should we plead guilty to the lesser count?",
    "What should we argue at trial?",
    "Do we settle before the hearing?",
    "What is our best strategy on these facts?",
    "Which defence should we run?",
    "Advise me what to do next on this matter.",
    "Should we call the defendant to give evidence?",
]

#: Genuine educational questions. These must NOT trigger the guard.
EDUCATIONAL = [
    "How does the defence of duress apply to the account in this file?",
    "What is the test for dishonesty after Ivey?",
    "What are the elements a court works through on causation?",
    "How does the burden of proof operate on a defence like this?",
    "What does the authority say about voluntary association and duress?",
    "How is the objective limb of duress assessed?",
    "What evidence would a court expect on the immediacy limb?",
    "How does section 76 relate to the common law on reasonable force?",
]

CITATION = re.compile(r"\[\d{4}\]|\bs\.\d+\b")
BARE_REFUSAL = re.compile(
    r"^(i (cannot|can't|am unable)|sorry|unfortunately)[^.]*\.$", re.IGNORECASE | re.DOTALL
)


class TestOutcomePredictionIsRedirected:
    @pytest.mark.parametrize("question", OUTCOME_PREDICTION)
    def test_it_is_classified_as_outcome_prediction(self, question):
        guard_class, rule_id = classify_question(question)
        assert guard_class is GuardClass.OUTCOME_PREDICTION, question
        assert rule_id and rule_id.startswith("OP-")

    @pytest.mark.parametrize("question", OUTCOME_PREDICTION)
    def test_it_is_redirected_to_the_legal_test(self, ask, question):
        body = ask(question).json()
        assert body["guard_triggered"] == GuardClass.OUTCOME_PREDICTION.value
        assert "does not predict" in body["content"]

    @pytest.mark.parametrize("question", OUTCOME_PREDICTION)
    def test_the_redirect_is_substantive_not_a_refusal(self, ask, question):
        content = ask(question).json()["content"]

        assert not BARE_REFUSAL.match(content.strip())
        assert len(content.split()) >= 150, "a redirect this short is a refusal with manners"
        # The elements are enumerated. How many there are depends on the test:
        # dishonesty has two limbs, duress has six. Enumeration is the property
        # being asserted, not a fixed count.
        enumerated = re.findall(r"^\d+\. ", content, re.MULTILINE)
        assert len(enumerated) >= 2, "the elements must be enumerated"
        assert "element" in content.lower()
        assert "burden" in content.lower() or "prove" in content.lower()
        assert "court" in content.lower()

    @pytest.mark.parametrize("question", OUTCOME_PREDICTION)
    def test_the_redirect_states_no_conclusion(self, ask, question):
        content = ask(question).json()["content"].lower()
        for phrase in ("will win", "will lose", "will succeed", "will be acquitted", "% chance"):
            assert phrase not in content

    def test_the_redirect_still_maps_the_case_facts_onto_the_elements(self, ask):
        body = ask(OUTCOME_PREDICTION[0]).json()
        assert body["case_facts_referenced"]
        assert "case file fact" in body["content"]
        assert "not as a conclusion about this matter" in body["content"]

    def test_guard_triggered_is_recorded_on_the_interaction(self, container, service_ask):
        service_ask(OUTCOME_PREDICTION[0])
        record = container.interactions.all_records()[-1]
        assert record.guard_triggered is GuardClass.OUTCOME_PREDICTION
        assert record.question_class == "outcome_prediction_redirect"


class TestLitigationStrategyIsRedirected:
    @pytest.mark.parametrize("question", LITIGATION_STRATEGY)
    def test_it_is_classified_as_litigation_strategy(self, question):
        guard_class, rule_id = classify_question(question)
        assert guard_class is GuardClass.LITIGATION_STRATEGY, question
        assert rule_id and rule_id.startswith("LS-")

    @pytest.mark.parametrize("question", LITIGATION_STRATEGY)
    def test_it_is_redirected_to_legal_principles(self, ask, question):
        body = ask(question).json()
        assert body["guard_triggered"] == GuardClass.LITIGATION_STRATEGY.value
        assert "decision for the conduct of the case" in body["content"]
        assert len(body["content"].split()) >= 150

    @pytest.mark.parametrize("question", LITIGATION_STRATEGY)
    def test_it_does_not_tell_the_learner_what_to_do(self, ask, question):
        content = ask(question).json()["content"].lower()
        for phrase in ("you should plead", "i recommend", "i advise", "the best course is"):
            assert phrase not in content

    def test_guard_triggered_is_recorded(self, container, service_ask):
        service_ask(LITIGATION_STRATEGY[0])
        record = container.interactions.all_records()[-1]
        assert record.guard_triggered is GuardClass.LITIGATION_STRATEGY
        assert record.question_class == "litigation_strategy_redirect"


class TestGenuineEducationalQuestionsDoNotTrigger:
    @pytest.mark.parametrize("question", EDUCATIONAL)
    def test_the_guard_stays_silent(self, question):
        guard_class, _ = classify_question(question)
        assert guard_class is GuardClass.NONE, question

    @pytest.mark.parametrize("question", EDUCATIONAL)
    def test_the_question_is_answered_normally(self, ask, question):
        body = ask(question).json()
        assert body["guard_triggered"] is None
        assert body["mode"] == "case_linked"

    def test_a_question_containing_the_word_win_is_not_over_caught(self):
        """The guard must not fire on the vocabulary alone."""
        assert classify_question("What does it mean to win an application to exclude evidence?")[0] is GuardClass.NONE
        assert classify_question("How does the court approach a settlement of this kind?")[0] is GuardClass.NONE


class TestTheGeneratorIsAlsoGuarded:
    def test_a_generated_outcome_prediction_is_caught_at_the_boundary(self, ask, container):
        container.generator.scenario = gen.OUTCOME_PREDICTION
        body = ask(EDUCATIONAL[0]).json()

        assert body["guard_triggered"] == GuardClass.OUTCOME_PREDICTION.value
        assert "your client will win at trial" not in body["content"].lower()
        assert "the court will find" not in body["content"].lower()

    def test_the_learner_gets_the_redirect_rather_than_nothing(self, ask, container):
        container.generator.scenario = gen.OUTCOME_PREDICTION
        body = ask(EDUCATIONAL[0]).json()
        assert len(body["content"].split()) >= 150
        assert "does not predict" in body["content"]

    def test_the_block_is_logged(self, container, service_ask, log_buffer):
        mark = log_buffer.tell()
        container.generator.scenario = gen.OUTCOME_PREDICTION
        service_ask(EDUCATIONAL[0])
        log_buffer.seek(mark)
        written = log_buffer.read()
        log_buffer.seek(0, 2)
        assert "case_coaching.output_prediction_blocked" in written


class TestTheGuardCannotBeTurnedOff:
    def test_no_configuration_key_can_disable_the_redirect(self):
        """Asserted over the whole configuration surface in
        tests/test_config_surface.py; repeated here at the behavioural level."""
        from uc06.composition import build_container

        from .conftest import make_settings

        for allow_dev in (True, False):
            for timeout in (1, 60_000):
                container = build_container(
                    make_settings(allow_dev_session_ids=allow_dev, generation_timeout_ms=timeout)
                )
                outcome = container.service.ask(
                    session_id="sess-level-5",
                    user_id="user-alice",
                    question=OUTCOME_PREDICTION[0],
                    case_file_id=cf.CASE_FULL,
                    request_id="r",
                )
                assert outcome.response.guard_triggered is GuardClass.OUTCOME_PREDICTION

    def test_the_guard_survives_a_classifier_outage(self, ask):
        """A classifier that is down does not mean an unguarded answer: the
        in-domain rule set is always available in-process."""
        body = ask("Will my client win at trial? " + TRIGGER_UNAVAILABLE).json()
        assert body["guard_triggered"] == GuardClass.OUTCOME_PREDICTION.value

    def test_the_guard_survives_a_classifier_timeout(self, ask):
        body = ask("Should we plead guilty? " + TRIGGER_TIMEOUT).json()
        assert body["guard_triggered"] == GuardClass.LITIGATION_STRATEGY.value

    def test_the_redirect_content_is_built_in_domain_not_by_the_generator(self, container, service_ask):
        """The generator is never even called for a redirect, so it cannot drift
        into the prediction the guard exists to prevent."""
        service_ask(OUTCOME_PREDICTION[0])
        assert container.generator.calls == []

    def test_the_guard_fires_before_generation_on_every_case_file_state(self, ask, container):
        container.generator.scenario = gen.TIMEOUT
        body = ask(OUTCOME_PREDICTION[0]).json()
        # Even with a dead generator, the redirect is delivered.
        assert body["guard_triggered"] == GuardClass.OUTCOME_PREDICTION.value
        assert len(body["content"].split()) >= 150


class TestRedirectCalibration:
    def test_the_advanced_redirect_cites_authorities_and_the_basic_one_does_not(self, ask):
        basic = ask(OUTCOME_PREDICTION[0], session_id="sess-level-3").json()["content"]
        advanced = ask(OUTCOME_PREDICTION[0], session_id="sess-level-7").json()["content"]
        assert not CITATION.search(basic)
        assert CITATION.search(advanced)
        assert "Doctrinal note" in advanced

    def test_the_redirect_selects_the_test_matching_the_question(self, ask):
        duress = ask("Will the duress defence succeed here?").json()
        assert duress["topic_tag"] == "duress"
        assert "defence of duress" in duress["content"]

        dishonesty = ask("What are our chances on the dishonesty count?").json()
        assert dishonesty["topic_tag"] == "dishonesty"
        assert "dishonesty" in dishonesty["content"]
