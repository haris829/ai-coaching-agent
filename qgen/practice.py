"""One question from a named course, and the marking of it.

Two sources, both already in the database and both scoped strictly to the course asked for:

- ``qgen_questions`` - what this package has generated.
- ``qb_questions`` - the platform's own bank, about 21 per course across all 33.

**A question is served from the course that was named, or not at all.** Never from a neighbouring
course because the named one had run out. Being handed a Medical Law question after asking for
Criminology is worse than being told Criminology has none left, because nothing on the page would
say which had happened.

WHY A REFERENCE CARRIES ITS TABLE
---------------------------------
A reference is ``qgen-42`` or ``qb-85d034b366014bf893e9bbd67f8ac0bd``. The two sources key their
rows independently and not even in the same type: ``qgen_questions.id`` is a ``bigint``,
``qb_questions.id`` is a 32-character hex string. Naming the table means a reference cannot be
read against the wrong one, which matters most at marking time - reading the wrong row means
marking against the wrong answer key.

It is also load-bearing at the SQL level. PostgreSQL will not compare a ``varchar`` column to an
integer, so treating a bank id as a number is not a subtle mismatch but ``operator does not
exist: character varying = integer`` on the first query.

WHAT IS SHOWN, AND WHAT IS ADMITTED
------------------------------------
676 of the bank's 706 questions are ``DRAFT`` - written by a model, not yet reviewed by a person.
They are served, because they are what the courses hold, but every one carries its status to the
page. The platform's own rule that only ``ACTIVE`` questions reach a learner belongs to a real
sitting; this is practice, and hiding the status would be the part that was wrong.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import psycopg

#: The two tables a reference can point at, and how it is spelled.
OWN = "qgen"
BANK = "qb"


@dataclass(frozen=True, slots=True)
class PracticeOption:
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class PracticeQuestion:
    """A question to answer. Never carries the key - see :func:`mark`."""

    ref: str
    question_text: str
    options: tuple[PracticeOption, ...]
    course: str | None
    #: ``DRAFT`` or ``ACTIVE``. Shown, not hidden.
    status: str
    source: str

    def as_dict(self) -> dict:
        return {
            "ref": self.ref,
            "question": self.question_text,
            "options": [{"label": o.label, "text": o.text} for o in self.options],
            "course": self.course,
            "status": self.status,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class Marked:
    correct: bool
    answer: str
    explanation: str | None
    status: str


def _refs_for(conn: psycopg.Connection, code: str, title: str) -> list[str]:
    """Every question reference available for this course, from both sources."""
    refs: list[str] = []
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM qgen_questions WHERE course_code = %s", (code,))
        refs += [f"{OWN}-{record['id']}" for record in cur.fetchall()]

        cur.execute(
            "SELECT q.id FROM qb_questions q "
            "JOIN qb_question_topics qt ON qt.question_id = q.id "
            "JOIN qb_topics t ON t.id = qt.topic_id "
            "WHERE lower(t.name) = lower(%s)",
            (title,),
        )
        refs += [f"{BANK}-{record['id']}" for record in cur.fetchall()]
    return refs


@dataclass(frozen=True, slots=True)
class Draw:
    """One draw from a course: the question, and how much of the course is left.

    ``pool`` and ``remaining`` come out of the same read as the question. Counting them in a
    second call would run the same two queries twice for every question served, and could
    disagree with itself if anything were written in between.
    """

    #: ``None`` when the course has nothing unseen left - the signal to generate a fresh one,
    #: never to reach into another course.
    question: PracticeQuestion | None
    #: Unseen questions still available after this one.
    remaining: int
    #: Everything this course has, seen or not.
    pool: int


def pick(
    conn: psycopg.Connection,
    *,
    code: str,
    title: str,
    exclude: set[str] | None = None,
    rng: random.Random | None = None,
) -> Draw:
    """One unseen question for this course.

    ``exclude`` is what the asker has already been shown. Held by the caller rather than in a
    table: it is the state of one sitting at one screen, it is meaningless an hour later, and a
    table of it would be a table nobody ever deletes from.
    """
    seen = exclude or set()
    everything = _refs_for(conn, code, title)
    available = [ref for ref in everything if ref not in seen]
    if not available:
        return Draw(question=None, remaining=0, pool=len(everything))

    chosen = (rng or random).choice(available)
    return Draw(
        question=read(conn, chosen, title),
        remaining=len(available) - 1,
        pool=len(everything),
    )


def split_ref(ref: str) -> tuple[str, int | str] | None:
    """A reference as (table, row id), or ``None`` if it names nothing.

    The two tables key their rows differently - ``qgen_questions.id`` is a ``bigint`` and
    ``qb_questions.id`` is a 32-character hex string - so the id is returned in the type its own
    table uses. PostgreSQL will not compare a ``varchar`` column to an integer, and a wrong guess
    here is not a silent mismatch but a hard ``operator does not exist`` at query time.
    """
    table, _, raw = (ref or "").partition("-")
    if not raw:
        return None
    if table == OWN:
        try:
            return OWN, int(raw)
        except ValueError:
            return None
    if table == BANK:
        return BANK, raw
    return None


def read(conn: psycopg.Connection, ref: str, course: str | None = None) -> PracticeQuestion | None:
    parsed = split_ref(ref)
    if parsed is None:
        return None
    table, row_id = parsed
    if table == OWN:
        return _read_own(conn, row_id, course)
    return _read_bank(conn, row_id, course)


def _read_own(conn: psycopg.Connection, row_id: int, course: str | None):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT q.question_text, q.status, coalesce(c.title, q.course_ref) AS course "
            "FROM qgen_questions q LEFT JOIN qc_courses c ON c.code = q.course_code "
            "WHERE q.id = %s",
            (row_id,),
        )
        record = cur.fetchone()
        if record is None:
            return None
        cur.execute(
            "SELECT label, option_text FROM qgen_question_options "
            "WHERE question_id = %s ORDER BY label",
            (row_id,),
        )
        options = tuple(
            PracticeOption(label=o["label"], text=o["option_text"]) for o in cur.fetchall()
        )
    return PracticeQuestion(
        ref=f"{OWN}-{row_id}",
        question_text=record["question_text"],
        options=options,
        course=record["course"] or course,
        status=record["status"],
        source="generated for this course",
    )


def _read_bank(conn: psycopg.Connection, row_id: str, course: str | None):
    with conn.cursor() as cur:
        cur.execute("SELECT question_text, status FROM qb_questions WHERE id = %s", (row_id,))
        record = cur.fetchone()
        if record is None:
            return None
        # Ordered by the bank's own `position`, because a true/false pair reads wrongly in
        # alphabetical order and the position column exists to say how it was written.
        cur.execute(
            "SELECT label, text FROM qb_question_options WHERE question_id = %s "
            "ORDER BY position, label",
            (row_id,),
        )
        options = tuple(PracticeOption(label=o["label"], text=o["text"]) for o in cur.fetchall())
    return PracticeQuestion(
        ref=f"{BANK}-{row_id}",
        question_text=record["question_text"],
        options=options,
        course=course,
        status=record["status"],
        source="course question bank",
    )


def mark(conn: psycopg.Connection, ref: str, label: str) -> Marked | None:
    """Mark one answer against the key held in the database.

    The key is read here and returned only in reply to an answer already given. It is never on
    the question itself, so the page cannot mark anything by itself.
    """
    parsed = split_ref(ref)
    if parsed is None:
        return None
    table, row_id = parsed
    given = (label or "").strip().upper()

    with conn.cursor() as cur:
        if table == OWN:
            cur.execute(
                "SELECT answer_label, explanation, status FROM qgen_questions WHERE id = %s",
                (row_id,),
            )
            record = cur.fetchone()
            if record is None:
                return None
            return Marked(
                correct=given == record["answer_label"],
                answer=record["answer_label"],
                explanation=record["explanation"],
                status=record["status"],
            )
        if table == BANK:
            cur.execute("SELECT explanation, status FROM qb_questions WHERE id = %s", (row_id,))
            record = cur.fetchone()
            if record is None:
                return None
            # `AND o.is_correct`, never `= 1`: SQLite would accept the comparison and PostgreSQL
            # refuses it outright.
            cur.execute(
                "SELECT label FROM qb_question_options WHERE question_id = %s AND is_correct "
                "ORDER BY position, label",
                (row_id,),
            )
            correct = [o["label"] for o in cur.fetchall()]
            if not correct:
                return None
            return Marked(
                correct=given in correct,
                answer=", ".join(correct),
                explanation=record["explanation"],
                status=record["status"],
            )
    return None
