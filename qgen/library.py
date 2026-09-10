"""Finding material in the database to answer a question from.

**Read-only, and the only file in this package that reads another capability's tables.** Keeping
that in one place is what makes the coupling reviewable: if the platform's question bank changes
shape, this file breaks and nothing else does.

WHERE THE MATERIAL COMES FROM
------------------------------
Two sources, both already in the database:

- ``qgen_questions`` - what this package has generated, with its explanations.
- ``qb_questions`` - the platform's question bank, 706 questions with an explanation on every
  one, tagged to topics whose names are the course titles.

Neither is a course description; the catalogue still has none. But an explanation written for a
course is real material about that course, which is a great deal more than a four-word title.

Nothing here writes. Not to ``qc_courses``, not to ``qb_*``, not to ``qz_*``.
"""

from __future__ import annotations

import psycopg

from .domain.answering import Snippet
from .domain.replies import collapse
from .domain.resolution import escape_like

#: How many rows to pull from each source before ranking them in Python.
#:
#: The database narrows by keyword and the ranking picks the best few. Doing the ranking in SQL
#: would mean a scoring expression per engine; doing it in Python costs one cheap query and
#: works the same everywhere.
CANDIDATE_LIMIT = 300


def _clauses(terms: tuple[str, ...], column: str) -> tuple[str, list[str]]:
    """An OR of case-insensitive contains-tests, and the parameters for it."""
    return (
        " OR ".join([f"{column} ILIKE %s ESCAPE '\\'"] * len(terms)),
        [escape_like(term.lower()) for term in terms],
    )


def find_material(
    conn: psycopg.Connection,
    terms: tuple[str, ...],
    *,
    course_title: str | None = None,
    course_code: str | None = None,
) -> list[Snippet]:
    """Material mentioning any of these keywords, newest first.

    **When a course is named, only that course's material is returned.** Not preferred - only.
    Falling back to the rest of the library when a course has nothing would answer a question
    about Criminology out of Medical Law and report it as course material, and the reader would
    have no way to tell. An empty result is the honest answer: the course holds nothing on this,
    which the caller then says out loud.
    """
    if not terms:
        return []

    found = _from_own_questions(conn, terms, course_code)
    found += _from_question_bank(conn, terms, course_title)
    return found


def _from_own_questions(
    conn: psycopg.Connection, terms: tuple[str, ...], course_code: str | None
) -> list[Snippet]:
    where, params = _clauses(terms, "q.question_text || ' ' || coalesce(q.explanation, '')")
    sql = (
        "SELECT q.question_text, q.explanation, coalesce(c.title, q.course_ref) AS course "
        "FROM qgen_questions q "
        "LEFT JOIN qc_courses c ON c.code = q.course_code "
        f"WHERE ({where}) "
    )
    if course_code:
        sql += "AND q.course_code = %s "
        params = [*params, course_code]
    sql += "ORDER BY q.id DESC LIMIT %s"

    with conn.cursor() as cur:
        cur.execute(sql, [*params, CANDIDATE_LIMIT])
        return [
            Snippet(
                source="generated for this course",
                course=record["course"],
                text=record["question_text"],
                explanation=record["explanation"],
            )
            for record in cur.fetchall()
        ]


def _from_question_bank(
    conn: psycopg.Connection, terms: tuple[str, ...], course_title: str | None
) -> list[Snippet]:
    """The platform's own question bank, joined to the topic that names the course.

    Read through the topic tag rather than a course column because the bank has no course
    column - the tag is the only link there is, and its names are the course titles.
    """
    where, params = _clauses(terms, "q.question_text || ' ' || coalesce(q.explanation, '')")
    sql = (
        "SELECT q.question_text, q.explanation, t.name AS course "
        "FROM qb_questions q "
        "LEFT JOIN qb_question_topics qt ON qt.question_id = q.id "
        "LEFT JOIN qb_topics t ON t.id = qt.topic_id "
        f"WHERE ({where}) "
    )
    if course_title:
        sql += "AND lower(t.name) = lower(%s) "
        params = [*params, course_title]
    sql += "ORDER BY q.id DESC LIMIT %s"

    with conn.cursor() as cur:
        cur.execute(sql, [*params, CANDIDATE_LIMIT])
        return [
            Snippet(
                source="course question bank",
                course=record["course"],
                text=record["question_text"],
                explanation=record["explanation"],
            )
            for record in cur.fetchall()
        ]


def guess_course(conn: psycopg.Connection, question: str) -> tuple[str, str] | None:
    """The course a question names, when it plainly names one.

    Exact containment of a course title in the question, longest title first so "International
    Trade Law" wins over "International Law". Nothing clever: a question that does not name a
    course is answered from the whole library, which is the right answer for "what is mens rea?"
    """
    text = collapse(question).casefold()
    if not text:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT code, title FROM qc_courses ORDER BY length(title) DESC")
        for record in cur.fetchall():
            if record["title"].casefold() in text:
                return record["code"], record["title"]
    return None
