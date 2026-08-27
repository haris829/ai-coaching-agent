"""Grounding, section identification, and the out-of-lesson path."""

from __future__ import annotations

from conftest import IN_LESSON_QUESTION, OFF_LESSON_QUESTION, build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.domain.enums import (
    Grounding,
    QuestionClass,
    ResponseAction,
    SectionRefStatus,
    SourceStatus,
)


def test_in_lesson_question_is_grounded_in_the_lesson(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    assert response.grounding is Grounding.LESSON
    assert response.section_reference.status is SectionRefStatus.RESOLVED
    assert response.section_reference.lesson_section_id == "sec_hearsay_definition"
    assert response.concept_tag == "hearsay"
    assert response.topic_tag == "evidence"


def test_section_reference_points_at_a_section_that_exists(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    lesson = harness.courses.get_lesson(fx.COURSE_EVIDENCE, fx.LESSON_HEARSAY)
    ids = {section.section_id for section in lesson.sections}
    assert response.section_reference.lesson_section_id in ids


def test_a_different_question_resolves_a_different_section(harness) -> None:
    first = harness.ask(IN_LESSON_QUESTION)
    second = harness.ask("What is the burden of proof on admissibility?")
    assert first.section_reference.lesson_section_id != second.section_reference.lesson_section_id
    assert second.section_reference.lesson_section_id == "sec_hearsay_proof"


def test_unresolvable_section_is_marked_not_guessed(harness) -> None:
    response = harness.ask(OFF_LESSON_QUESTION)
    assert response.section_reference.status is SectionRefStatus.UNRESOLVED
    assert response.section_reference.lesson_section_id is None


def test_off_lesson_question_is_answered_not_refused(harness) -> None:
    response = harness.ask(OFF_LESSON_QUESTION)
    assert response.explanation.strip()
    assert response.grounding is Grounding.GENERAL_KNOWLEDGE


def test_off_lesson_question_is_signalled_clearly(harness) -> None:
    response = harness.ask(OFF_LESSON_QUESTION)
    assert "not covered" in response.explanation.lower()
    assert "general knowledge" in response.explanation.lower()
    assert response.notice and "not covered" in response.notice.lower()


def test_off_lesson_question_offers_the_free_form_affordance(harness) -> None:
    """A structured action the caller can act on, not a sentence of prose."""
    response = harness.ask(OFF_LESSON_QUESTION)
    assert ResponseAction.START_FREE_FORM_SESSION in response.actions


def test_in_lesson_answer_does_not_offer_the_free_form_affordance(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    assert ResponseAction.START_FREE_FORM_SESSION not in response.actions


def test_grounding_is_recorded_on_the_interaction(harness) -> None:
    in_lesson = harness.ask(IN_LESSON_QUESTION)
    off_lesson = harness.ask(OFF_LESSON_QUESTION)
    assert harness.interactions.get(in_lesson.interaction_id).grounding is Grounding.LESSON
    record = harness.interactions.get(off_lesson.interaction_id)
    assert record.grounding is Grounding.GENERAL_KNOWLEDGE
    assert record.question_class is QuestionClass.OUT_OF_LESSON


def test_a_sparse_section_does_not_produce_a_lesson_grounded_claim() -> None:
    """Nothing curated to transform means the answer is not presented as the lesson's."""
    harness = build_harness()
    response = harness.ask(
        "What is without prejudice correspondence?", lesson_id=fx.LESSON_SPARSE
    )
    assert response.grounding is Grounding.GENERAL_KNOWLEDGE
    assert response.source_status["lesson"] in (SourceStatus.AVAILABLE, SourceStatus.PARTIAL)
