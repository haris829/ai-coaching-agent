"""Conformance suite for the ``AnswerGenerator`` port."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ..domain.enums import ExplanationProfile, FramingStrategy, Grounding
from ..domain.models import GenerationRequest, GenerationResult, LessonConcept, LessonSection


@dataclass(frozen=True)
class GeneratorScenarios:
    section: LessonSection
    concept: LessonConcept
    quotable_spans: tuple[str, ...]
    #: Text present in the lesson body that must never appear in generated output.
    forbidden_body_text: tuple[str, ...] = ()


class AnswerGeneratorConformance:
    @pytest.fixture
    def adapter(self):  # pragma: no cover - overridden by the implementer
        raise NotImplementedError("provide an `adapter` fixture")

    @pytest.fixture
    def scenarios(self) -> GeneratorScenarios:  # pragma: no cover - overridden
        raise NotImplementedError("provide a `scenarios` fixture")

    def _request(self, scenarios: GeneratorScenarios, **overrides) -> GenerationRequest:
        base: dict = dict(
            question="what does this mean",
            profile=ExplanationProfile.INTERMEDIATE,
            framing=FramingStrategy.FIRST_PRINCIPLES,
            grounding=Grounding.LESSON,
            lesson_title="A Lesson",
            course_title="A Course",
            section=scenarios.section,
            concept=scenarios.concept,
            quotable_spans=scenarios.quotable_spans,
            budget_exhausted=False,
            candidate_cross_lesson_refs=(),
            prompt_id="lesson_grounded_explanation",
            prompt_version="test",
        )
        base.update(overrides)
        return GenerationRequest(**base)

    def test_returns_structure_not_prose(self, adapter, scenarios) -> None:
        result = adapter.generate(self._request(scenarios))
        assert isinstance(result, GenerationResult)
        assert result.explanation.strip()

    def test_honours_the_requested_framing(self, adapter, scenarios) -> None:
        """Framing is the service's decision. A generator that ignores it breaks non-repetition."""
        for framing in FramingStrategy:
            result = adapter.generate(self._request(scenarios, framing=framing))
            assert result.framing_used is framing

    def test_each_framing_produces_different_text(self, adapter, scenarios) -> None:
        outputs = {
            adapter.generate(self._request(scenarios, framing=f)).explanation for f in FramingStrategy
        }
        assert len(outputs) == len(FramingStrategy), "each framing must change the approach"

    def test_profile_changes_the_output(self, adapter, scenarios) -> None:
        basic = adapter.generate(self._request(scenarios, profile=ExplanationProfile.BASIC)).explanation
        advanced = adapter.generate(
            self._request(scenarios, profile=ExplanationProfile.ADVANCED)
        ).explanation
        assert basic != advanced

    def test_quotes_only_the_supplied_spans(self, adapter, scenarios) -> None:
        """Budgeted spans may be reproduced. Nothing else from the lesson may be."""
        if not scenarios.forbidden_body_text:
            pytest.skip("no forbidden body text supplied")
        result = adapter.generate(self._request(scenarios))
        for forbidden in scenarios.forbidden_body_text:
            assert forbidden not in result.explanation, (
                "lesson body prose must never be reproduced; only budgeted spans may be quoted"
            )

    def test_empty_budget_does_not_fall_back_to_the_body(self, adapter, scenarios) -> None:
        result = adapter.generate(self._request(scenarios, quotable_spans=(), budget_exhausted=True))
        assert result.explanation.strip()
        for forbidden in scenarios.forbidden_body_text:
            assert forbidden not in result.explanation

    def test_general_knowledge_does_not_claim_lesson_provenance(self, adapter, scenarios) -> None:
        result = adapter.generate(
            self._request(scenarios, grounding=Grounding.GENERAL_KNOWLEDGE, quotable_spans=())
        )
        lowered = result.explanation.lower()
        assert "not covered" in lowered or "general knowledge" in lowered

    def test_is_deterministic(self, adapter, scenarios) -> None:
        first = adapter.generate(self._request(scenarios)).explanation
        second = adapter.generate(self._request(scenarios)).explanation
        assert first == second

    def test_never_references_an_unoffered_lesson(self, adapter, scenarios) -> None:
        result = adapter.generate(self._request(scenarios, candidate_cross_lesson_refs=()))
        assert result.cross_lesson_refs == (), (
            "with no candidates offered, no cross-lesson reference may be produced"
        )
