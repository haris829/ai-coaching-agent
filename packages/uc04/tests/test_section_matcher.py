"""Section matcher behaviours carried over from the reference implementation's checklist."""

from __future__ import annotations

from uc04.adapters.mock import fixtures as fx
from uc04.core.section_matcher import MatchAnchor, SectionMatcher

LESSON = fx.LESSONS[fx.LESSON_HEARSAY]
MATCHER = SectionMatcher()


def test_identifies_the_section_that_actually_covers_the_question() -> None:
    result = MATCHER.match("What does hearsay actually mean?", LESSON)
    assert result.best is not None
    assert result.best.section.section_id == "sec_hearsay_definition"
    assert result.best.concept is not None
    assert result.best.concept.concept_tag == "hearsay"


def test_picks_the_right_concept_when_one_section_teaches_several() -> None:
    """sec_hearsay_proof carries both burden_of_proof and standard_of_proof."""
    burden = MATCHER.match("Who carries the burden of proof on admissibility?", LESSON)
    standard = MATCHER.match("What is the standard of proof in civil proceedings?", LESSON)

    assert burden.best is not None and standard.best is not None
    assert burden.best.section.section_id == "sec_hearsay_proof"
    assert standard.best.section.section_id == "sec_hearsay_proof"
    assert burden.best.concept.concept_tag == "burden_of_proof"
    assert standard.best.concept.concept_tag == "standard_of_proof"


def test_returns_no_match_for_an_off_lesson_question() -> None:
    assert MATCHER.match("How do I renew my practising certificate?", LESSON).best is None


def test_does_not_latch_onto_a_section_that_merely_reuses_an_everyday_word() -> None:
    """"answer" appears in the lesson's prose; the question is not about the lesson."""
    result = MATCHER.match("What is the answer to question 4?", LESSON)
    assert result.best is None
    for match in result.ranked:
        assert match.anchor is not MatchAnchor.NAME or match.score < 0.35


def test_only_ever_returns_sections_and_concepts_present_in_the_supplied_lesson() -> None:
    section_ids = {section.section_id for section in LESSON.sections}
    concept_tags = {concept.concept_tag for concept in LESSON.concepts}

    for question in (
        "hearsay",
        "exception business records",
        "burden and standard of proof",
        "something entirely unrelated to this course",
        "",
        "the the the",
    ):
        result = MATCHER.match(question, LESSON)
        for match in result.ranked:
            assert match.section.section_id in section_ids
            if match.concept is not None:
                assert match.concept.concept_tag in concept_tags


def test_finds_a_concept_by_id_and_refuses_an_unknown_one() -> None:
    found = MATCHER.find_concept("hearsay", LESSON)
    assert found is not None
    assert found.section.section_id == "sec_hearsay_definition"
    assert MATCHER.find_concept("concept_that_does_not_exist", LESSON) is None


def test_handles_a_lesson_with_no_sections_without_throwing() -> None:
    bare = LESSON.model_copy(update={"sections": (), "concepts": ()})
    result = MATCHER.match("anything at all", bare)
    assert result.best is None
    assert result.ranked == ()


def test_handles_an_empty_question_without_throwing() -> None:
    result = MATCHER.match("", LESSON)
    assert result.best is None
    assert result.ranked == ()


def test_is_deterministic_across_repeated_calls() -> None:
    first = MATCHER.match("How is hearsay defined?", LESSON)
    second = MATCHER.match("How is hearsay defined?", LESSON)
    assert first.best is not None and second.best is not None
    assert first.best.section.section_id == second.best.section.section_id
    assert first.best.score == second.best.score
    assert [m.section.section_id for m in first.ranked] == [m.section.section_id for m in second.ranked]
