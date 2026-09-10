"""Reading ``qc_courses``. Read-only, deliberately.

The catalogue belongs to another capability. This package reads it and writes its own tables
(``qgen_*``); it never writes a course row, and it never touches the sibling project's ``qb_*``
or ``qz_*`` tables. One-way traffic is what makes the dependency reviewable - and it means a
mistake here cannot corrupt the thing everything else reads.

The lookup rules live in :mod:`qgen.domain.resolution`, which has no database in it. This file is
the three queries that feed them.
"""

from __future__ import annotations

import psycopg

from .domain.generation import CourseBrief
from .domain.resolution import (
    CourseRow,
    choose,
    escape_like,
    normalise_reference,
    wants_partial_match,
)

#: Every column the generator can use. ``description``, ``rqf_level`` and ``subject_area`` are
#: NULL on all 33 rows today; they are selected anyway so that the day the platform import runs,
#: the questions improve without a code change.
_COLUMNS = "code, title, description, rqf_level, subject_area"


def _row(record: dict) -> CourseRow:
    return CourseRow(
        code=record["code"],
        title=record["title"],
        description=record.get("description"),
        rqf_level=record.get("rqf_level"),
        subject_area=record.get("subject_area"),
    )


def list_courses(conn: psycopg.Connection) -> list[CourseRow]:
    """Every course, by title.

    Ordered by title rather than by code because a person choosing one reads the name; nobody
    outside this system knows that ``LL-34590`` is Criminology.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM qc_courses ORDER BY title")
        return [_row(record) for record in cur.fetchall()]


def find_course(conn: psycopg.Connection, course_ref: str) -> CourseRow | None:
    """The one course this reference names, or ``None`` if none or more than one does.

    Three queries, narrowest first. The third is skipped entirely for a short reference, and its
    ``LIMIT 2`` is the point of the query: the only thing worth knowing is whether exactly one row
    matches, and fetching the other thirty to discover that would be waste.
    """
    reference = normalise_reference(course_ref)
    if not reference:
        return None
    lowered = reference.lower()

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM qc_courses WHERE lower(code) = %s LIMIT 2", (lowered,)
        )
        by_code = [_row(record) for record in cur.fetchall()]

        cur.execute(
            f"SELECT {_COLUMNS} FROM qc_courses WHERE lower(title) = %s LIMIT 2", (lowered,)
        )
        by_title = [_row(record) for record in cur.fetchall()]

        by_partial: list[CourseRow] = []
        if wants_partial_match(reference):
            cur.execute(
                f"SELECT {_COLUMNS} FROM qc_courses "
                "WHERE lower(title) LIKE %s ESCAPE '\\' LIMIT 2",
                (escape_like(lowered),),
            )
            by_partial = [_row(record) for record in cur.fetchall()]

    return choose(by_code=by_code, by_title=by_title, by_partial=by_partial)


def brief_for(course_ref: str, course: CourseRow | None) -> CourseBrief:
    """The brief to generate from: the matched course, or the caller's own words.

    A miss is not an error. The caller asked for questions on a subject and named a course as the
    way of saying which; if the name does not resolve, the subject still stands. What must not
    happen is the miss going unmentioned, so :attr:`CourseBrief.grounding` records which of the
    two this is and every report repeats it.
    """
    if course is None:
        return CourseBrief(code=None, name=normalise_reference(course_ref))
    return CourseBrief(
        code=course.code,
        name=course.title,
        description=course.description,
        rqf_level=course.rqf_level,
        subject_area=course.subject_area,
    )
