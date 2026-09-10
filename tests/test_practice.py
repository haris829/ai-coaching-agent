"""Practice questions: drawn from the named course, a different one each time, marked server-side.

The rule under most of this file is that a question comes from the course that was asked for or
does not come at all. Being handed a Medical Law question after asking for Criminology is worse
than being told Criminology has run out, because nothing on the page would say which happened.
"""

from __future__ import annotations

import random

import pytest

from qgen import practice, storage
from qgen.domain.generation import CourseBrief, GeneratedOption, GeneratedQuestion

CRIMINOLOGY = ("LL-34590", "Criminology (Postgraduate)")
MEDICAL = ("LL-45165", "Medical Law MA (Postgraduate)")


def a_question(text: str, answer: str = "B") -> GeneratedQuestion:
    return GeneratedQuestion(
        question_text=text,
        options=tuple(
            GeneratedOption(label=label, text=f"option {label}", is_correct=label == answer)
            for label in ("A", "B", "C", "D")
        ),
        explanation="Because the Act says so.",
    )


@pytest.fixture
def db(conn):
    storage.ensure_schema(conn)
    return conn


def store(db, code: str, title: str, *texts: str) -> list[int]:
    brief = CourseBrief(code=code, name=title)
    run_id = storage.open_run(
        db, course_ref=title, brief=brief, requested=len(texts), asked_for=len(texts), model="t"
    )
    return storage.store_questions(
        db,
        run_id=run_id,
        course_ref=title,
        brief=brief,
        questions=tuple(a_question(text) for text in texts),
    )


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def test_a_course_with_material_yields_a_question(db):
    draw = practice.pick(db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1])

    assert draw.question is not None
    assert draw.question.question_text
    assert draw.question.options
    assert 0 <= draw.remaining < draw.pool


def test_the_question_comes_from_the_course_that_was_asked_for(db):
    """Checked against a course whose questions are unmistakable."""
    store(db, *MEDICAL, "A distinctly medical question?")

    for _ in range(6):
        assert practice.pick(db, code=MEDICAL[0], title=MEDICAL[1]).question.course == MEDICAL[1]


def test_a_question_never_carries_its_answer(db):
    question = practice.pick(db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1]).question

    payload = question.as_dict()
    assert "answer" not in payload
    assert all("is_correct" not in option for option in payload["options"])


def test_the_draft_status_travels_with_the_question_rather_than_being_hidden(db):
    question = practice.pick(db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1]).question

    assert question.status in {"DRAFT", "ACTIVE"}


def test_a_question_already_seen_is_not_served_again(db):
    first = practice.pick(db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1]).question

    for _ in range(10):
        again = practice.pick(
            db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1], exclude={first.ref}
        ).question
        assert again.ref != first.ref


def test_asking_repeatedly_walks_the_whole_course_without_repeating(db):
    seen: set[str] = set()
    pool = practice.pick(db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1]).pool

    for _ in range(pool):
        draw = practice.pick(db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1], exclude=seen)
        assert draw.question is not None, "ran out before the pool was exhausted"
        assert draw.question.ref not in seen
        seen.add(draw.question.ref)

    assert len(seen) == pool
    assert draw.remaining == 0


def test_an_exhausted_course_returns_nothing_rather_than_another_courses_question(db):
    """The signal to write a new one - never to reach into a neighbouring course."""
    everything = set(practice._refs_for(db, *CRIMINOLOGY))

    draw = practice.pick(db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1], exclude=everything)

    assert draw.question is None
    assert draw.remaining == 0
    assert draw.pool == len(everything)


def test_a_course_with_nothing_at_all_returns_nothing(db):
    draw = practice.pick(db, code="NO-SUCH", title="No Such Course")

    assert (draw.question, draw.remaining, draw.pool) == (None, 0, 0)


def test_the_choice_is_random_rather_than_always_the_newest(db):
    """"A different one each time" has to hold for the second ask, not only across a session."""
    picks = {
        practice.pick(
            db, code=CRIMINOLOGY[0], title=CRIMINOLOGY[1], rng=random.Random(seed)
        ).question.ref
        for seed in range(12)
    }

    assert len(picks) > 1


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_a_reference_names_the_table_it_came_from(db):
    ids = store(db, *CRIMINOLOGY, "Whose question is this?")

    question = practice.read(db, f"qgen-{ids[0]}")

    assert question.ref == f"qgen-{ids[0]}"
    assert question.question_text == "Whose question is this?"


def test_the_two_sources_cannot_be_confused_for_one_another(db):
    """Row 12 of one table is a different question from row 12 of the other, and marking the
    wrong one means marking against the wrong answer key."""
    own = practice.read(db, "qgen-1")
    bank = practice.read(db, "qb-1")

    if own and bank:
        assert own.question_text != bank.question_text or own.ref != bank.ref


@pytest.mark.parametrize("ref", ["", "nonsense", "qgen-", "qgen-abc", "other-1", "qgen-99999999"])
def test_a_reference_that_points_at_nothing_reads_as_nothing(db, ref):
    assert practice.read(db, ref) is None


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------


def test_a_right_answer_is_marked_right(db):
    ids = store(db, *CRIMINOLOGY, "Which one?")

    result = practice.mark(db, f"qgen-{ids[0]}", "B")

    assert result.correct is True
    assert result.answer == "B"
    assert result.explanation == "Because the Act says so."
    assert result.status == "DRAFT"


def test_a_wrong_answer_is_marked_wrong_and_names_the_right_one(db):
    ids = store(db, *CRIMINOLOGY, "Which one?")

    result = practice.mark(db, f"qgen-{ids[0]}", "A")

    assert result.correct is False
    assert result.answer == "B"


def test_marking_is_case_insensitive_and_tolerates_spacing(db):
    ids = store(db, *CRIMINOLOGY, "Which one?")

    assert practice.mark(db, f"qgen-{ids[0]}", " b ").correct is True


def test_a_bank_question_is_marked_against_the_banks_own_key(db):
    """`AND is_correct`, never `= 1`: PostgreSQL refuses the integer comparison outright."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT q.id, o.label FROM qb_questions q "
            "JOIN qb_question_options o ON o.question_id = q.id "
            "WHERE o.is_correct LIMIT 1"
        )
        record = cur.fetchone()
    if record is None:
        pytest.skip("the question bank has no options")

    result = practice.mark(db, f"qb-{record['id']}", record["label"])

    assert result.correct is True
    assert record["label"] in result.answer


@pytest.mark.parametrize("ref", ["qgen-99999999", "qb-99999999", "nonsense", "qgen-x"])
def test_marking_something_that_does_not_exist_is_nothing_not_a_guess(db, ref):
    assert practice.mark(db, ref, "A") is None
