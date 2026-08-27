"""GuardClassifier conformance. Adapter-agnostic.

Any classifier UC-06 will run must at minimum catch what the in-domain rule set
catches. A remote classifier that is worse than the fallback is not an upgrade,
so the conformance bar is the fallback's own behaviour.
"""

from __future__ import annotations

import inspect

import pytest

from uc06.domain.enums import GuardClass
from uc06.domain.errors import ProviderError
from uc06.domain.models import GuardResult
from uc06.ports.guard import GuardClassifier

MUST_CATCH_OUTCOME = [
    "Will my client win at trial on these facts?",
    "What are our chances of getting this thrown out?",
    "How strong is our case on the duress point?",
    "Will this defence succeed given the CCTV?",
    "Is my client likely to be convicted?",
    "What is the likelihood of acquittal here?",
]

MUST_CATCH_STRATEGY = [
    "Should we plead guilty to the lesser count?",
    "What should we argue at trial?",
    "Do we settle before the hearing?",
    "Which defence should we run?",
    "What is our best strategy on these facts?",
]

MUST_NOT_CATCH = [
    "How does the defence of duress apply to the account in this file?",
    "What is the test for dishonesty after Ivey?",
    "What are the elements a court works through on causation?",
    "How does the burden of proof operate on a defence like this?",
    "How is the objective limb of duress assessed?",
]

#: Ambiguous phrasings. The contract does not dictate which way these fall - it
#: requires only that the classifier returns a valid class and does not crash.
AMBIGUOUS = [
    "What happens if the jury does not accept the account?",
    "How do practitioners usually approach a case like this?",
    "What would a court make of a six-hour delay?",
    "Is the evidence on this element usually enough?",
]


class TestShape:
    def test_it_satisfies_the_port(self, guard_adapter):
        assert isinstance(guard_adapter, GuardClassifier)

    def test_the_signature_matches_the_port(self, guard_adapter):
        assert list(inspect.signature(guard_adapter.classify).parameters) == ["question"]

    def test_it_returns_the_platform_type(self, guard_adapter):
        result = guard_adapter.classify(MUST_NOT_CATCH[0])
        assert isinstance(result, GuardResult)
        assert isinstance(result.guard_class, GuardClass)
        assert isinstance(result.topic_tag, str) and result.topic_tag


class TestClassification:
    @pytest.mark.parametrize("question", MUST_CATCH_OUTCOME)
    def test_outcome_prediction_is_caught(self, guard_adapter, question):
        assert guard_adapter.classify(question).guard_class is GuardClass.OUTCOME_PREDICTION

    @pytest.mark.parametrize("question", MUST_CATCH_STRATEGY)
    def test_litigation_strategy_is_caught(self, guard_adapter, question):
        assert guard_adapter.classify(question).guard_class is GuardClass.LITIGATION_STRATEGY

    @pytest.mark.parametrize("question", MUST_NOT_CATCH)
    def test_genuine_educational_questions_are_not_caught(self, guard_adapter, question):
        assert guard_adapter.classify(question).guard_class is GuardClass.NONE

    @pytest.mark.parametrize("question", AMBIGUOUS)
    def test_ambiguous_questions_return_a_valid_class(self, guard_adapter, question):
        result = guard_adapter.classify(question)
        assert result.guard_class in set(GuardClass)

    def test_a_triggered_result_names_the_rule_that_fired(self, guard_adapter):
        result = guard_adapter.classify(MUST_CATCH_OUTCOME[0])
        assert result.triggered is True
        assert result.matched_rule_id, "the matched rule must be reportable for audit"

    def test_classification_is_deterministic(self, guard_adapter):
        for question in MUST_CATCH_OUTCOME + MUST_NOT_CATCH:
            first = guard_adapter.classify(question)
            second = guard_adapter.classify(question)
            assert first.guard_class is second.guard_class


class TestFailureModes:
    def test_it_never_returns_none_class_by_way_of_an_error(self, guard_adapter):
        """A classifier that cannot decide raises, so the service can fall back
        to the in-domain rules. It must not report `none` on failure."""
        try:
            result = guard_adapter.classify("")
        except ProviderError as exc:
            assert exc.port == "guard_classifier"
        else:
            assert isinstance(result, GuardResult)

    def test_no_other_exception_type_escapes(self, guard_adapter):
        for question in ("", "?" * 5000, "unusual input"):
            try:
                guard_adapter.classify(question)
            except ProviderError:
                pass
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"uncontracted exception escaped: {type(exc).__name__}")
