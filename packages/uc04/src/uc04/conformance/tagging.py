"""Conformance suites for the ``ConceptTagger`` and ``QuizIntentClassifier`` ports."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ..domain.enums import UNCLASSIFIED, QuizIntentLabel
from ..domain.models import ConceptTag, LessonContent, QuizIntentResult
from ..domain.vocabularies import TOPIC_VOCABULARY, is_known_concept


@dataclass(frozen=True)
class ConceptTaggerScenarios:
    lesson: LessonContent | None
    #: A question whose concept is in the closed vocabulary, and the tag it must produce.
    in_vocabulary_question: str
    expected_concept_tag: str
    #: A question about something the vocabulary does not cover.
    out_of_vocabulary_question: str


class ConceptTaggerConformance:
    @pytest.fixture
    def adapter(self):  # pragma: no cover - overridden by the implementer
        raise NotImplementedError("provide an `adapter` fixture")

    @pytest.fixture
    def scenarios(self) -> ConceptTaggerScenarios:  # pragma: no cover - overridden
        raise NotImplementedError("provide a `scenarios` fixture")

    def test_returns_domain_model(self, adapter, scenarios) -> None:
        tag = adapter.tag(scenarios.in_vocabulary_question, scenarios.lesson)
        assert isinstance(tag, ConceptTag)

    def test_in_vocabulary_question_gets_the_expected_tag(self, adapter, scenarios) -> None:
        tag = adapter.tag(scenarios.in_vocabulary_question, scenarios.lesson)
        assert tag.concept_tag == scenarios.expected_concept_tag
        assert tag.matched is True

    def test_tags_come_from_the_closed_vocabularies(self, adapter, scenarios) -> None:
        """Free-form tags make later aggregation meaningless. Only known values may be emitted."""
        for question in (scenarios.in_vocabulary_question, scenarios.out_of_vocabulary_question):
            tag = adapter.tag(question, scenarios.lesson)
            assert tag.concept_tag == UNCLASSIFIED or is_known_concept(tag.concept_tag)
            assert tag.topic_tag == UNCLASSIFIED or tag.topic_tag in TOPIC_VOCABULARY

    def test_unmatched_question_becomes_unclassified(self, adapter, scenarios) -> None:
        tag = adapter.tag(scenarios.out_of_vocabulary_question, scenarios.lesson)
        assert tag.concept_tag == UNCLASSIFIED
        assert tag.matched is False

    def test_empty_question_does_not_raise(self, adapter, scenarios) -> None:
        tag = adapter.tag("", scenarios.lesson)
        assert tag.concept_tag == UNCLASSIFIED

    def test_is_deterministic(self, adapter, scenarios) -> None:
        first = adapter.tag(scenarios.in_vocabulary_question, scenarios.lesson)
        second = adapter.tag(scenarios.in_vocabulary_question, scenarios.lesson)
        assert first == second


@dataclass(frozen=True)
class QuizClassifierScenarios:
    lesson: LessonContent | None
    #: Phrasings that plainly ask for an answer. All must be detected.
    direct_answer_seeking: tuple[str, ...]
    #: Indirect phrasings. All must be detected.
    indirect_answer_seeking: tuple[str, ...]
    #: Genuine concept questions. None may be classified as answer seeking.
    genuine_learning: tuple[str, ...]
    #: Attempts to override the system's own rules.
    injection_attempts: tuple[str, ...] = field(default_factory=tuple)


class QuizIntentClassifierConformance:
    @pytest.fixture
    def adapter(self):  # pragma: no cover - overridden by the implementer
        raise NotImplementedError("provide an `adapter` fixture")

    @pytest.fixture
    def scenarios(self) -> QuizClassifierScenarios:  # pragma: no cover - overridden
        raise NotImplementedError("provide a `scenarios` fixture")

    def test_returns_domain_model(self, adapter, scenarios) -> None:
        result = adapter.classify(scenarios.direct_answer_seeking[0], scenarios.lesson)
        assert isinstance(result, QuizIntentResult)
        assert result.label in {label.value for label in QuizIntentLabel}
        assert 0.0 <= result.confidence <= 1.0

    def test_direct_answer_seeking_is_detected(self, adapter, scenarios) -> None:
        for question in scenarios.direct_answer_seeking:
            result = adapter.classify(question, scenarios.lesson)
            assert result.label == QuizIntentLabel.QUIZ_ANSWER_REQUEST.value, question

    def test_indirect_answer_seeking_is_detected(self, adapter, scenarios) -> None:
        for question in scenarios.indirect_answer_seeking:
            result = adapter.classify(question, scenarios.lesson)
            assert result.label == QuizIntentLabel.QUIZ_ANSWER_REQUEST.value, question

    def test_genuine_learning_is_not_flagged_as_answer_seeking(self, adapter, scenarios) -> None:
        for question in scenarios.genuine_learning:
            result = adapter.classify(question, scenarios.lesson)
            assert result.label != QuizIntentLabel.QUIZ_ANSWER_REQUEST.value, question

    def test_injection_attempts_are_detected(self, adapter, scenarios) -> None:
        if not scenarios.injection_attempts:
            pytest.skip("no injection scenarios supplied")
        for question in scenarios.injection_attempts:
            result = adapter.classify(question, scenarios.lesson)
            assert result.label == QuizIntentLabel.QUIZ_ANSWER_REQUEST.value, question

    def test_signals_carry_no_lesson_content(self, adapter, scenarios) -> None:
        """Signals are named reasons for audit, not extracts of anything."""
        result = adapter.classify(scenarios.direct_answer_seeking[0], scenarios.lesson)
        for signal in result.signals:
            assert " " not in signal, "signals should be stable identifiers, not prose"
            assert len(signal) < 60

    def test_is_deterministic(self, adapter, scenarios) -> None:
        question = scenarios.direct_answer_seeking[0]
        assert adapter.classify(question, scenarios.lesson) == adapter.classify(question, scenarios.lesson)
