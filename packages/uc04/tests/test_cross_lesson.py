"""Cross-lesson references: verified against the course structure, or stripped."""

from __future__ import annotations

from conftest import IN_LESSON_QUESTION, build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.core.cross_lesson import verify_references
from uc04.domain.models import CourseStructure, CrossLessonRef, GenerationResult


def _generator_returning(refs: tuple[CrossLessonRef, ...]):
    class FabricatingGenerator:
        def generate(self, request):  # noqa: ANN001, ANN201
            return GenerationResult(
                explanation="An explanation of the concept, with references attached.",
                section_id=request.section.section_id if request.section else None,
                concept_tag=request.concept.concept_tag if request.concept else None,
                cross_lesson_refs=refs,
                framing_used=request.framing,
            )

    return FabricatingGenerator()


def test_a_valid_reference_is_included(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    assert response.cross_lesson_references
    structure = harness.courses.get_course_structure(fx.COURSE_EVIDENCE)
    for ref in response.cross_lesson_references:
        assert structure.contains(ref.lesson_id)


def test_a_fabricated_lesson_reference_is_stripped() -> None:
    """The failure class this guards is a fabricated citation: it must not reach the learner."""
    fabricated = (CrossLessonRef(lesson_id=fx.LESSON_GHOST, title="A Lesson That Does Not Exist"),)
    harness = build_harness(generator=_generator_returning(fabricated))

    response = harness.ask(IN_LESSON_QUESTION)

    assert response.cross_lesson_references == ()
    serialised = response.model_dump_json()
    assert fx.LESSON_GHOST not in serialised
    assert "Does Not Exist" not in serialised


def test_a_plausible_but_wrong_title_cannot_survive() -> None:
    """The title is taken from the course structure, not from the generator."""
    mistitled = (CrossLessonRef(lesson_id=fx.LESSON_WITNESS, title="Completely Invented Title"),)
    harness = build_harness(generator=_generator_returning(mistitled))

    response = harness.ask(IN_LESSON_QUESTION)

    assert len(response.cross_lesson_references) == 1
    ref = response.cross_lesson_references[0]
    assert ref.lesson_id == fx.LESSON_WITNESS
    assert ref.title == "Witness Evidence, Competence and Compellability"
    assert "Invented" not in response.model_dump_json()


def test_references_never_cross_a_course_boundary() -> None:
    """A lesson from another course cannot resolve against this course's structure."""
    other_course = (CrossLessonRef(lesson_id="lesson_from_another_course", title="Elsewhere"),)
    harness = build_harness(generator=_generator_returning(other_course))

    response = harness.ask(IN_LESSON_QUESTION)
    assert response.cross_lesson_references == ()


def test_a_self_reference_is_stripped() -> None:
    self_ref = (CrossLessonRef(lesson_id=fx.LESSON_HEARSAY, title="The Rule Against Hearsay"),)
    harness = build_harness(generator=_generator_returning(self_ref))

    response = harness.ask(IN_LESSON_QUESTION)
    assert response.cross_lesson_references == ()


def test_no_reference_survives_when_the_structure_could_not_be_loaded() -> None:
    """Nothing can be verified, so nothing may be referenced."""
    structure = CourseStructure(course_id=fx.COURSE_EVIDENCE, title="Evidence", lessons=())
    outcome = verify_references(
        (CrossLessonRef(lesson_id=fx.LESSON_WITNESS, title="Witness Evidence"),),
        None,
        fx.LESSON_HEARSAY,
    )
    assert outcome.verified == ()
    assert outcome.stripped == (fx.LESSON_WITNESS,)

    empty = verify_references(
        (CrossLessonRef(lesson_id=fx.LESSON_WITNESS, title="Witness Evidence"),),
        structure,
        fx.LESSON_HEARSAY,
    )
    assert empty.verified == ()


def test_duplicate_references_are_collapsed() -> None:
    ref = CrossLessonRef(lesson_id=fx.LESSON_WITNESS, title="Witness Evidence")
    structure = fx.COURSE_STRUCTURES[fx.COURSE_EVIDENCE]
    outcome = verify_references((ref, ref, ref), structure, fx.LESSON_HEARSAY)
    assert len(outcome.verified) == 1


def test_the_generator_is_only_offered_real_lessons(harness) -> None:
    """Defence in depth: the candidate list itself comes from the loaded structure."""
    captured: list = []

    class CapturingGenerator:
        def generate(self, request):  # noqa: ANN001, ANN201
            captured.append(request.candidate_cross_lesson_refs)
            return GenerationResult(
                explanation="An explanation.",
                section_id=request.section.section_id if request.section else None,
                concept_tag=request.concept.concept_tag if request.concept else None,
                cross_lesson_refs=(),
                framing_used=request.framing,
            )

    harness = build_harness(generator=CapturingGenerator())
    harness.ask(IN_LESSON_QUESTION)

    structure = fx.COURSE_STRUCTURES[fx.COURSE_EVIDENCE]
    assert captured
    for candidates in captured:
        for candidate in candidates:
            assert structure.contains(candidate.lesson_id)
            assert candidate.lesson_id != fx.LESSON_HEARSAY
