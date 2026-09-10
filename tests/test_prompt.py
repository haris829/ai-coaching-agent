"""The prompt, the batch split, and the headroom. All pure, so all cheap to pin down."""

from __future__ import annotations

import pytest

from qgen.domain.generation import (
    ANGLES,
    AVOID_STEM_CHARS,
    MAX_AVOID_STEMS,
    MAX_QUESTIONS_PER_REQUEST,
    CourseBrief,
    batch_sizes,
    build_prompt,
    with_headroom,
)

TITLE_ONLY = CourseBrief(code="LL-34590", name="Criminology (Postgraduate)")
FULL = CourseBrief(
    code="LL-45165",
    name="Medical Law MA (Postgraduate)",
    description="Consent, capacity and clinical negligence.",
    rqf_level=7,
    subject_area="Medical Law",
    modules=("Consent", "Capacity", "Negligence"),
)


def test_the_prompt_names_the_course_and_the_count():
    prompt = build_prompt(TITLE_ONLY, 7)

    assert "Write 7 multiple-choice questions" in prompt
    assert "Course: Criminology (Postgraduate)" in prompt


def test_every_rule_the_parser_enforces_is_in_the_prompt():
    """The model is told exactly what it will be judged against.

    Asking for something the parser does not check wastes the model's effort; checking something
    the prompt never asked for guarantees refusals nobody can explain.
    """
    prompt = build_prompt(TITLE_ONLY, 5)

    for rule in [
        "exactly 4 options",
        "labelled A, B, C, D",
        "exactly one option is correct",
        "plausible",
        "all of the above",
        "none of the above",
        "which is NOT",
        "no two questions may test the same point",
        "stand alone",
        "one sentence of explanation",
        "Return ONLY a JSON object",
    ]:
        assert rule in prompt, rule


def test_the_json_shape_is_shown_rather_than_described():
    prompt = build_prompt(TITLE_ONLY, 1)

    assert '"questions"' in prompt
    assert '"options": {"A": "...", "B": "...", "C": "...", "D": "..."}' in prompt
    assert '"answer": "B"' in prompt


def test_the_level_and_description_are_passed_through_when_the_catalogue_has_them():
    prompt = build_prompt(FULL, 5)

    assert "Level: RQF 7" in prompt
    assert "Subject area: Medical Law" in prompt
    assert "Description: Consent, capacity and clinical negligence." in prompt
    assert "  - Consent" in prompt


def test_a_course_with_no_description_says_nothing_about_one():
    """No invented level, no invented subject area. Absent means absent."""
    prompt = build_prompt(TITLE_ONLY, 5)

    assert "Level:" not in prompt
    assert "Description:" not in prompt
    assert "Subject area:" not in prompt


def test_an_angle_is_added_only_when_one_is_given():
    assert "focus on" not in build_prompt(TITLE_ONLY, 5)
    assert f"focus on {ANGLES[0]}" in build_prompt(TITLE_ONLY, 5, angle=ANGLES[0])


def test_there_are_five_angles_and_they_are_distinct():
    assert len(ANGLES) == 5
    assert len(set(ANGLES)) == 5


def test_previously_asked_questions_are_listed_with_an_instruction_not_to_rephrase():
    prompt = build_prompt(TITLE_ONLY, 5, avoid=("What is actus reus?",))

    assert "already been asked" in prompt
    assert "Do not rephrase" in prompt
    assert "  - What is actus reus?" in prompt


def test_the_avoid_list_is_capped_so_it_cannot_crowd_out_the_instruction():
    stems = tuple(f"Question number {n}?" for n in range(200))

    prompt = build_prompt(TITLE_ONLY, 5, avoid=stems)

    assert prompt.count("  - Question number") == MAX_AVOID_STEMS
    # Newest first: the caller passes them in that order and the cap must drop the tail.
    assert "  - Question number 0?" in prompt
    assert "  - Question number 199?" not in prompt


def test_a_long_previous_question_is_truncated_rather_than_dropped():
    prompt = build_prompt(TITLE_ONLY, 5, avoid=("x" * 500,))

    assert "x" * AVOID_STEM_CHARS in prompt
    assert "x" * (AVOID_STEM_CHARS + 1) not in prompt


def test_no_avoid_section_appears_when_the_course_has_no_history():
    assert "already been asked" not in build_prompt(TITLE_ONLY, 5)


# ---------------------------------------------------------------------------
# Batching and headroom
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, ()),
        (1, (1,)),
        (10, (10,)),
        (11, (10, 1)),
        (50, (10, 10, 10, 10, 10)),
        (23, (10, 10, 3)),
    ],
)
def test_a_request_is_split_into_batches_of_ten(count, expected):
    assert batch_sizes(count) == expected


def test_batches_always_sum_to_what_was_asked_for():
    for count in range(1, MAX_QUESTIONS_PER_REQUEST + 1):
        assert sum(batch_sizes(count)) == count


def test_no_headroom_is_added_when_the_course_has_no_history():
    """Nothing to repeat means nothing will be dropped, so paying for extra would be waste."""
    assert with_headroom(10, has_history=False) == 10


def test_headroom_covers_the_repeats_that_will_be_dropped():
    assert with_headroom(10, has_history=True) == 14
    assert with_headroom(1, has_history=True) == 2


def test_headroom_never_exceeds_the_per_request_ceiling():
    assert with_headroom(50, has_history=True) == MAX_QUESTIONS_PER_REQUEST
