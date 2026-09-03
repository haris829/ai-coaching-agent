"""Is the company's course content rich enough to generate quiz questions from?

    python -m scripts.assess_course_content

The company's answer to "where do the questions come from" is: generate them from the course
content. That is a reasonable design, and whether it produces a defensible assessment depends
entirely on how much content each course actually has. A quiz generated from two sentences of
marketing copy is a quiz nobody should certify anybody with.

So this measures, rather than assumes. It reports, for the fields that could plausibly feed a
generator, how many courses have them and how long they are — and prints the richest and thinnest
examples so a human can judge the material with their own eyes.

Read-only, and reads content columns of ``uni_courses`` only — no learner data exists in this
database to read. Credentials come from the environment, as in the sibling scripts.
"""

from __future__ import annotations

import os
import statistics
import sys
from typing import Any

# The Windows console defaults to cp1252, which cannot encode an em-dash. Their course text is
# full of non-ASCII punctuation, so a survey script must not die on it.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")

#: The columns that could feed a question generator, in the order a generator would value them.
CONTENT_FIELDS = (
    "course_description",
    "skills_gained",
    "specialisms",
    "eligibility_criteria",
    "prerequisites",
    "leads_to_qualification",
)


def _connect() -> Any:
    try:
        import pyodbc
    except ImportError:  # pragma: no cover
        sys.exit("pyodbc is not installed")

    missing = [
        name
        for name in ("COMPANY_DB_SERVER", "COMPANY_DB_NAME", "COMPANY_DB_USER", "COMPANY_DB_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        sys.exit(f"set these first: {', '.join(missing)}")

    driver = next((name for name in DRIVERS if name in set(pyodbc.drivers())), None)
    if driver is None:
        sys.exit("install ODBC Driver 18 for SQL Server")

    return pyodbc.connect(
        f"DRIVER={{{driver}}};"
        f"SERVER={os.environ['COMPANY_DB_SERVER']};"
        f"DATABASE={os.environ['COMPANY_DB_NAME']};"
        f"UID={os.environ['COMPANY_DB_USER']};"
        f"PWD={os.environ['COMPANY_DB_PASSWORD']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30",
        readonly=True,
        timeout=90,
    )


def main() -> int:
    connection = _connect()
    cursor = connection.cursor()

    total = cursor.execute("SELECT COUNT(*) FROM dbo.uni_courses").fetchone()[0]
    print(f"uni_courses: {total} rows\n")

    print("How many courses have each content field, and how long is it?")
    print("-" * 74)
    print(f"{'field':<26}{'populated':>12}{'median':>10}{'p90':>9}{'max':>9}")
    for field in CONTENT_FIELDS:
        lengths = [
            row[0]
            for row in cursor.execute(
                f"SELECT LEN({field}) FROM dbo.uni_courses "
                f"WHERE {field} IS NOT NULL AND LEN(LTRIM(RTRIM({field}))) > 0"
            )
        ]
        if not lengths:
            print(f"{field:<26}{'0':>12}{'—':>10}{'—':>9}{'—':>9}")
            continue
        lengths.sort()
        p90 = lengths[min(len(lengths) - 1, int(len(lengths) * 0.9))]
        share = f"{len(lengths)} ({100 * len(lengths) // total}%)"
        print(
            f"{field:<26}{share:>12}"
            f"{int(statistics.median(lengths)):>10}{p90:>9}{lengths[-1]:>9}"
        )

    # A generator needs *substantive* text. 500 characters is roughly a full paragraph — below that
    # there is not enough to write a defensible question from, let alone twenty.
    substantive = cursor.execute(
        "SELECT COUNT(*) FROM dbo.uni_courses "
        "WHERE course_description IS NOT NULL AND LEN(course_description) >= 500"
    ).fetchone()[0]
    print(
        f"\ncourses with a description of 500+ characters: {substantive}"
        f"  ({100 * substantive // total}% of {total})"
    )

    print("\n\nTHE RICHEST COURSE DESCRIPTION")
    print("-" * 74)
    row = cursor.execute(
        "SELECT TOP 1 course_name, uni_name, LEN(course_description), course_description "
        "FROM dbo.uni_courses WHERE course_description IS NOT NULL "
        "ORDER BY LEN(course_description) DESC"
    ).fetchone()
    if row:
        print(f"{str(row[0]).strip()} — {row[1]}  ({row[2]} chars)\n")
        print(" ".join(str(row[3]).split())[:1200])

    print("\n\nA TYPICAL (MEDIAN-LENGTH) COURSE DESCRIPTION")
    print("-" * 74)
    row = cursor.execute(
        """
        SELECT TOP 1 course_name, uni_name, LEN(course_description), course_description
        FROM dbo.uni_courses
        WHERE course_description IS NOT NULL
          AND LEN(course_description) BETWEEN 200 AND 400
        ORDER BY id
        """
    ).fetchone()
    if row:
        print(f"{str(row[0]).strip()} — {row[1]}  ({row[2]} chars)\n")
        print(" ".join(str(row[3]).split())[:1200])
    else:
        print("(no course description falls in that range)")

    print("\n\nSUPPORTING MATERIAL: reading lists")
    print("-" * 74)
    books = cursor.execute(
        "SELECT COUNT(*), COUNT(DISTINCT course_id) FROM dbo.course_books_aids"
    ).fetchone()
    print(f"course_books_aids: {books[0]} rows across {books[1]} distinct course_id values")
    print("(a reading list names a source; it is not itself content a question can be written from)")

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
