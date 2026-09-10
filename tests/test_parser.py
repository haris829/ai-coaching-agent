"""What the parser refuses, which matters more than what it accepts.

A parser that accepts a malformed question has not saved a question; it has hidden a defect
inside a certificate. So the acceptance cases are three tests and the refusal cases are the rest
of the file.

Every case here asserts two things: that the question did not survive, and that nothing was
repaired into existence in its place.
"""

from __future__ import annotations

import json

import pytest

from qgen.domain.generation import (
    OPTION_LABELS,
    GeneratedQuestion,
    parse_questions,
    stem_key,
)


def reply(*questions: dict) -> str:
    return json.dumps({"questions": list(questions)})


def good(
    text: str = "Which limitation period applies to a claim in contract?",
    answer: str = "B",
    **overrides,
) -> dict:
    question = {
        "question": text,
        "options": {"A": "three years", "B": "six years", "C": "one year", "D": "twelve years"},
        "answer": answer,
        "explanation": "Section 5 of the Limitation Act 1980 sets six years.",
    }
    question.update(overrides)
    return question


# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------


def test_a_well_formed_question_survives_with_its_key_and_explanation():
    report = parse_questions(reply(good()), wanted=1)

    assert report.count == 1
    assert report.refused == 0
    question = report.accepted[0]
    assert question.answer_label == "B"
    assert [option.label for option in question.options] == list(OPTION_LABELS)
    assert [option.is_correct for option in question.options] == [False, True, False, False]
    assert question.explanation.startswith("Section 5")


def test_json_inside_a_fenced_code_block_is_still_read():
    fenced = "```json\n" + reply(good()) + "\n```"

    assert parse_questions(fenced, wanted=1).count == 1


def test_a_lower_case_answer_letter_is_read_as_the_label():
    # Not a repair: the letter names one of the four options unambiguously, and rejecting "b"
    # would throw away a question that is entirely well formed.
    report = parse_questions(reply(good(answer="b")), wanted=1)

    assert report.accepted[0].answer_label == "B"


# ---------------------------------------------------------------------------
# Refusing - the reply as a whole
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Q1. Which limitation period applies? A. three years B. six years Answer: B",
        "{not json at all}",
        '{"questions": "six of them"}',
        '{"questions": []}',
        "[]",
    ],
    ids=["empty", "blank", "prose", "broken-json", "not-a-list", "empty-list", "not-an-object"],
)
def test_a_reply_that_carries_no_questions_yields_nothing(text):
    report = parse_questions(text, wanted=5)

    assert report.accepted == ()
    assert report.refusals != ()


def test_the_prose_format_is_refused_rather_than_scraped():
    """The format the model returns when asked in prose. Nothing is salvaged from it.

    Scraping ``Answer: B`` out of prose is where a wrong key comes from: the labels drift, an
    explanation runs across lines, and a stem containing the word "Answer" is read as the key.
    """
    prose = (
        "Q1. Which limitation period applies to a claim in contract?\n"
        "A. three years\nB. six years\nC. one year\nD. twelve years\nAnswer: B\n"
    )

    assert parse_questions(prose, wanted=1).count == 0


# ---------------------------------------------------------------------------
# Refusing - one question at a time
# ---------------------------------------------------------------------------


def test_a_question_with_three_options_is_refused_not_padded_to_four():
    three = good()
    del three["options"]["D"]

    report = parse_questions(reply(three), wanted=1)

    assert report.count == 0
    assert report.refused == 1
    assert report.refusals == (("a question was missing option D", 1),)


def test_a_question_with_five_options_is_refused():
    five = good()
    five["options"]["E"] = "eighteen years"

    report = parse_questions(reply(five), wanted=1)

    assert report.count == 0
    assert report.refusals == (("a question had options outside A-D", 1),)


def test_an_empty_option_is_refused():
    report = parse_questions(reply(good(options={"A": "a", "B": "", "C": "c", "D": "d"})), wanted=1)

    assert report.count == 0
    assert report.refusals == (("a question was missing option B", 1),)


def test_two_identical_options_are_refused():
    duplicated = good()
    duplicated["options"]["C"] = duplicated["options"]["A"]

    report = parse_questions(reply(duplicated), wanted=1)

    assert report.count == 0
    assert report.refusals == (("a question had duplicate options", 1),)


@pytest.mark.parametrize("answer", ["E", "", "6", "B and C", None, 2])
def test_an_answer_that_is_not_one_of_a_to_d_is_refused_not_guessed(answer):
    report = parse_questions(reply(good(answer=answer)), wanted=1)

    assert report.count == 0
    assert report.refusals == (("a question's answer was not one of A-D", 1),)


def test_a_question_with_no_stem_is_refused():
    report = parse_questions(reply(good(text="   ")), wanted=1)

    assert report.count == 0
    assert report.refusals == (("a question had no text", 1),)


def test_a_question_with_no_options_object_is_refused():
    naked = good()
    naked["options"] = ["three years", "six years"]

    report = parse_questions(reply(naked), wanted=1)

    assert report.count == 0
    assert report.refusals == (("a question had no options object", 1),)


def test_an_entry_that_is_not_an_object_is_refused():
    report = parse_questions('{"questions": ["a question", 7]}', wanted=2)

    assert report.count == 0
    assert report.refusals == (("an entry was not an object", 2),)


# ---------------------------------------------------------------------------
# Refusing - repeats
# ---------------------------------------------------------------------------


def test_a_question_repeated_inside_one_reply_is_kept_once():
    report = parse_questions(reply(good(), good()), wanted=5)

    assert report.count == 1
    assert report.refusals == (("a question repeated one already asked", 1),)


def test_a_repeat_differing_only_in_case_and_spacing_is_still_a_repeat():
    report = parse_questions(
        reply(good(), good(text="which  LIMITATION period applies to a claim in CONTRACT?")),
        wanted=5,
    )

    assert report.count == 1


def test_a_question_already_asked_for_this_course_is_refused():
    """The prompt asks the model not to repeat itself. This is the check that it did not."""
    history = frozenset({stem_key("Which limitation period applies to a claim in contract?")})

    report = parse_questions(reply(good()), wanted=5, already_asked=history)

    assert report.count == 0
    assert report.refusals == (("a question repeated one already asked", 1),)


# ---------------------------------------------------------------------------
# Counting, and the cap
# ---------------------------------------------------------------------------


def test_more_questions_than_asked_for_are_discarded():
    many = reply(*[good(text=f"Question number {n}?") for n in range(10)])

    assert parse_questions(many, wanted=3).count == 3


def test_refusals_are_counted_by_reason_so_a_low_yield_is_diagnosable():
    missing = good(text="One?")
    del missing["options"]["D"]
    another = good(text="Two?")
    del another["options"]["D"]

    report = parse_questions(reply(missing, another, good(text="Three?", answer="Z")), wanted=5)

    assert report.refused == 3
    assert dict(report.refusals) == {
        "a question was missing option D": 2,
        "a question's answer was not one of A-D": 1,
    }


def test_good_questions_survive_alongside_refused_ones():
    broken = good(text="Broken?", answer="E")

    report = parse_questions(reply(broken, good(text="Sound?")), wanted=5)

    assert report.count == 1
    assert report.accepted[0].question_text == "Sound?"
    assert report.refused == 1


def test_whitespace_in_a_stem_is_normalised_but_the_text_is_not_otherwise_touched():
    report = parse_questions(reply(good(text="  Which   period\napplies?  ")), wanted=1)

    assert report.accepted[0].question_text == "Which period applies?"


def test_the_key_is_the_normalised_stem():
    question = GeneratedQuestion(question_text="  A  Question? ", options=())

    assert question.key == "a question?"
