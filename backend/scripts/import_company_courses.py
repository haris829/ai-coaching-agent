"""Replace the demo course data with the company's real legal courses.

    python -m scripts.import_company_courses --dry-run     # show what it would import
    python -m scripts.import_company_courses               # import

WHY THIS EXISTS
---------------
The system seeds a course called "Test Course" and a quiz called "Test Quiz" so the workflow is
demonstrable on a laptop with no other systems attached. The company looked at that and said, fairly,
that it is dummy data. This script reads their real course catalogue — ``dbo.uni_courses`` in
``larry-legal`` — and puts real legal courses in its place, so a reviewer sees "Immigration Law LLM"
rather than "Test Course".

WHAT IT READS, AND WHAT IT WILL NOT
-----------------------------------
Reads: ``uni_courses`` only, and only the catalogue columns needed to name a course — id, name,
level, qualification, provider, law category. It issues a single ``SELECT``.

**It never reads learner data**, because there is none: ``larry-legal`` has no users, enrolments,
attempts or results tables. That absence is why a genuinely "real" end-to-end test is not yet
possible, and this script does not paper over it — it fixes the course half only.

WHAT IT WRITES
--------------
Rows in **our own** ``qc_courses`` and ``qc_quizzes``, in our own database. It does not write to the
company's database at all — the account we hold is read-only and, more to the point, their catalogue
is not ours to modify.

Idempotent on the course code, so running it twice imports nothing the second time. It leaves any
existing configuration version untouched: a quiz that has already been configured keeps its rules,
because republishing a version behind a reviewer's back is exactly the kind of surprise UC-01's
immutable versioning exists to prevent.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

#: Their catalogue holds 60,384 rows, most of them unrelated degree listings. A review needs a
#: legible handful of genuinely legal courses, not the whole scrape.
DEFAULT_LIMIT = 25

DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")

#: Only rows that look like a real, current, law-related offering. ``is_active`` is their own flag;
#: the category test keeps out the medicine and engineering degrees the scraper also collected.
SELECT_COURSES = """
    WITH ranked AS (
        SELECT c.id,
               c.course_name,
               c.course_level,
               c.qualification,
               c.uni_name,
               c.law_category,
               c.law_subcategory,
               c.subject_area,
               ROW_NUMBER() OVER (
                   PARTITION BY c.law_category
                   -- Prefer a named provider and a postgraduate/professional offering: those read
                   -- as real courses to a reviewer, where a scraped undergraduate listing does not.
                   ORDER BY CASE WHEN c.uni_name IS NOT NULL THEN 0 ELSE 1 END,
                            CASE WHEN c.course_level LIKE '%ostgrad%' THEN 0 ELSE 1 END,
                            LEN(c.course_name)
               ) AS rank_in_category
        FROM dbo.uni_courses AS c
        WHERE c.course_name IS NOT NULL
          AND LEN(LTRIM(RTRIM(c.course_name))) > 3
          AND (c.is_active = 1 OR c.is_active IS NULL)
          AND c.law_category IS NOT NULL
    )
    SELECT TOP (?)
           id, course_name, course_level, qualification,
           uni_name, law_category, law_subcategory, subject_area
    FROM ranked
    -- One course per legal category, so the catalogue a reviewer sees spans their taxonomy
    -- instead of showing the same degree from the same university twelve times.
    WHERE rank_in_category = 1
    ORDER BY law_category
"""


def _company_rows(limit: int) -> list[dict[str, Any]]:
    """The company's real courses, read-only."""
    try:
        import pyodbc
    except ImportError:  # pragma: no cover - environment problem
        sys.exit("pyodbc is not installed: python -m pip install pyodbc")

    missing = [
        name
        for name in (
            "COMPANY_DB_SERVER",
            "COMPANY_DB_NAME",
            "COMPANY_DB_USER",
            "COMPANY_DB_PASSWORD",
        )
        if not os.environ.get(name)
    ]
    if missing:
        sys.exit(f"set these environment variables first: {', '.join(missing)}")

    driver = next((name for name in DRIVERS if name in set(pyodbc.drivers())), None)
    if driver is None:
        sys.exit("install the SQL Server ODBC driver: winget install --id Microsoft.msodbcsql.18")

    connection = pyodbc.connect(
        f"DRIVER={{{driver}}};"
        f"SERVER={os.environ['COMPANY_DB_SERVER']};"
        f"DATABASE={os.environ['COMPANY_DB_NAME']};"
        f"UID={os.environ['COMPANY_DB_USER']};"
        f"PWD={os.environ['COMPANY_DB_PASSWORD']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30",
        readonly=True,
        timeout=60,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(SELECT_COURSES, limit)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def _clean(value: Any, limit: int) -> str | None:
    """Collapse whitespace and strip the scraper's leftovers.

    Their ``course_name`` values arrive with stray double quotes and trailing spaces — an artefact
    of however they were scraped. Cleaning them here rather than in the model keeps the fix at the
    boundary where the mess actually is.
    """
    if value is None:
        return None
    text = " ".join(str(value).split()).strip("\"'“” ")
    if not text:
        return None
    return text[:limit]


def _course_code(row: dict[str, Any]) -> str:
    """A stable code derived from their primary key, so re-running matches the same row."""
    return f"LL-{row['id']}"


def _course_title(row: dict[str, Any]) -> str:
    """The course as a reviewer should see it named."""
    name = _clean(row.get("course_name"), 200) or f"Course {row['id']}"
    qualification = _clean(row.get("qualification"), 40)
    if qualification and qualification.lower() not in name.lower():
        name = f"{name} ({qualification})"
    return name[:255]


def _quiz_title(row: dict[str, Any]) -> str:
    topic = (
        _clean(row.get("law_subcategory"), 80)
        or _clean(row.get("law_category"), 80)
        or _clean(row.get("subject_area"), 80)
        or "Course"
    )
    level = _clean(row.get("course_level"), 40)
    suffix = f" · {level}" if level else ""
    return f"{topic} knowledge check{suffix}"[:255]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be imported and write nothing",
    )
    args = parser.parse_args()

    rows = _company_rows(args.limit)
    print(f"read {len(rows)} course(s) from the company catalogue\n")
    for row in rows:
        print(f"  {_course_code(row):<12} {_course_title(row)}")
        print(f"  {'':<12} quiz: {_quiz_title(row)}")
        print(
            f"  {'':<12} provider: {_clean(row.get('uni_name'), 80) or '—'}"
            f"  ·  level: {_clean(row.get('course_level'), 40) or '—'}"
            f"  ·  category: {_clean(row.get('law_category'), 60) or '—'}"
        )
        print()

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    # Imported here so --dry-run works without touching our database at all.
    from app.db.session import session_scope
    from app.modules.quiz_configuration.models import Course, Quiz

    created_courses = 0
    created_quizzes = 0
    with session_scope() as session:
        for row in rows:
            code = _course_code(row)
            course = session.query(Course).filter(Course.code == code).one_or_none()
            if course is None:
                course = Course(code=code, title=_course_title(row))
                session.add(course)
                session.flush()
                created_courses += 1

            slug = f"{code.lower()}-check"
            quiz = session.query(Quiz).filter(Quiz.slug == slug).one_or_none()
            if quiz is None:
                session.add(
                    Quiz(course_id=course.id, slug=slug, title=_quiz_title(row))
                )
                created_quizzes += 1

    print(f"imported {created_courses} course(s) and {created_quizzes} quiz(zes)")
    print("existing configuration versions were left untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
