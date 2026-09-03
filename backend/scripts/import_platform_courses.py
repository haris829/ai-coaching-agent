"""Import the company's **platform** courses — the ten real ones — into our own database.

    python -m scripts.import_platform_courses --dry-run    # show what it would import
    python -m scripts.import_platform_courses              # import

WHY THIS EXISTS ALONGSIDE ``import_company_courses.py``
-------------------------------------------------------
Two of their databases hold something called courses, and they are not the same thing:

* ``larry-legal`` (SQL Server) — ``dbo.uni_courses``, **60,384 rows**. A scraped catalogue of other
  institutions' degree listings. 0.7% have a description. It is marketing data, and generating a
  professional assessment from a row of it would be generating from a course title alone.
* ``loopholelarry-dev`` (PostgreSQL) — ``public.courses``, **10 rows**, with descriptions, levels
  and a ``course_modules`` table. These are the courses their platform actually teaches.

Ten real courses with syllabuses beat sixty thousand titles, and they are what "generate quizzes
from our courses" means. That is what this script reads.

COLUMN NAMES ARE DISCOVERED, NOT ASSUMED
----------------------------------------
Their platform schema is Prisma-generated, so its columns are quoted camelCase (``"rqfLevel"``) and
the exact spellings are theirs to change. Rather than hardcode names and fail on a mismatch, this
reads ``information_schema.columns`` first and selects only the columns that are actually there,
against a list of ones it knows what to do with. A renamed column degrades the brief; it does not
break the import.

WHAT IT READS, AND WHAT IT WILL NOT
-----------------------------------
Two ``SELECT``s against ``public.courses`` and ``public.course_modules``, in a **read-only**
session. Nothing else — not ``users``, not ``questions``, not ``question_options``. Their data is
theirs; we are reading a course list to seed our own.

WHAT IT WRITES
--------------
Rows in **our** ``qc_courses`` and ``qc_quizzes``, in **our** database, keyed ``LLP-<their id>``.
Idempotent: re-running updates the brief fields on a course we already imported and creates nothing
twice. Existing configuration versions are left alone, because republishing a version behind a
reviewer's back is what UC-01's immutable versioning exists to prevent.

The module titles are folded into the course description under a "Modules covered:" heading. That
is a shaping decision made here, at the boundary, on purpose: a syllabus is the single most useful
thing a question generator can be told, and the alternative — a modules table of our own, mirroring
theirs — would be a second copy of their data to keep in step for no gain.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

#: Columns we know what to do with, in preference order per role. Their schema may spell these in
#: snake_case or camelCase depending on how the table was created; both are tried.
CANDIDATES: dict[str, tuple[str, ...]] = {
    "id": ("id", "courseId", "course_id"),
    "title": ("title", "name", "courseName", "course_name"),
    "description": ("description", "summary", "overview", "shortDescription"),
    "rqf_level": ("rqfLevel", "rqf_level", "level", "qualificationLevel"),
    "subject_area": ("subjectArea", "subject_area", "category", "subject", "discipline"),
}

MODULE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "course_id": ("courseId", "course_id"),
    "title": ("title", "name", "moduleName", "module_name"),
    "position": ("position", "order", "sequence", "orderIndex", "sortOrder"),
}


def _connection():
    """A read-only session on their platform database."""
    try:
        import psycopg
    except ImportError:  # pragma: no cover - environment problem
        sys.exit("psycopg is not installed: python -m pip install 'psycopg[binary]'")

    url = os.environ.get("COMPANY_POSTGRES_URL")
    if not url:
        sys.exit(
            "set COMPANY_POSTGRES_URL first, e.g.\n"
            "  $env:COMPANY_POSTGRES_URL = "
            "'postgresql://user:password@host:5432/loopholelarry-dev?sslmode=require'"
        )
    # read-only at the session level, so a mistake in this script cannot write to their database
    # even if somebody later adds a statement that tries to.
    return psycopg.connect(
        url,
        autocommit=True,
        connect_timeout=30,
        options="-c default_transaction_read_only=on",
    )


def _columns(cursor, table: str) -> dict[str, str]:
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {row[0].lower(): row[0] for row in cursor.fetchall()}


def _resolve(present: dict[str, str], candidates: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Map each role we care about to the column that actually exists, if any."""
    resolved: dict[str, str] = {}
    for role, names in candidates.items():
        for name in names:
            actual = present.get(name.lower())
            if actual:
                resolved[role] = actual
                break
    return resolved


def _select(cursor, table: str, mapping: dict[str, str], limit: int | None = None) -> list[dict]:
    projection = ", ".join(f'"{column}" AS "{role}"' for role, column in mapping.items())
    statement = f'SELECT {projection} FROM public."{table}"'
    if limit:
        statement += f" LIMIT {int(limit)}"
    cursor.execute(statement)
    roles = list(mapping)
    return [dict(zip(roles, row, strict=True)) for row in cursor.fetchall()]


def _clean(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip("\"'“” ")
    return text[:limit] or None


def _level(value: Any) -> int | None:
    """An RQF level as an integer, or nothing.

    Their column may hold ``6``, ``"6"`` or ``"Level 6"``. Anything outside RQF's own 1–8 range is
    discarded rather than guessed at: a wrong level would pitch every generated question at the
    wrong difficulty, which is worse than pitching them at no stated level.
    """
    if value is None:
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    if not digits:
        return None
    level = int(digits[:1]) if len(digits) > 2 else int(digits)
    return level if 1 <= level <= 8 else None


def _brief(course: dict, modules: list[str]) -> str | None:
    """The description a generator is given: their own text, plus the syllabus."""
    parts: list[str] = []
    description = _clean(course.get("description"), 4000)
    if description:
        parts.append(description)
    if modules:
        parts.append("Modules covered: " + "; ".join(modules))
    return "\n\n".join(parts) or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with _connection() as connection:
        cursor = connection.cursor()

        course_columns = _columns(cursor, "courses")
        if not course_columns:
            sys.exit("public.courses is not visible to this account")
        course_map = _resolve(course_columns, CANDIDATES)
        if "id" not in course_map or "title" not in course_map:
            sys.exit(
                "public.courses has no recognisable id/title column; found: "
                + ", ".join(sorted(course_columns.values()))
            )
        print("courses columns used : " + ", ".join(f"{r}={c}" for r, c in course_map.items()))
        missing = [role for role in CANDIDATES if role not in course_map]
        if missing:
            # Said out loud rather than silently degraded: a missing description or level means
            # measurably weaker questions, and whoever runs this should know that happened.
            print(f"not found (briefs will be thinner): {', '.join(missing)}")

        courses = _select(cursor, "courses", course_map, limit=args.limit)

        modules_by_course: dict[Any, list[str]] = {}
        module_columns = _columns(cursor, "course_modules")
        module_map = _resolve(module_columns, MODULE_CANDIDATES)
        if {"course_id", "title"} <= set(module_map):
            for row in _select(cursor, "course_modules", module_map):
                title = _clean(row.get("title"), 200)
                if title:
                    modules_by_course.setdefault(row["course_id"], []).append(title)
            print(f"modules              : {sum(len(v) for v in modules_by_course.values())}")
        else:
            print("modules              : no usable course_modules table")

    print(f"\nread {len(courses)} platform course(s)\n")
    prepared: list[dict[str, Any]] = []
    for course in courses:
        modules = modules_by_course.get(course["id"], [])
        prepared.append(
            {
                "code": f"LLP-{course['id']}",
                "title": _clean(course.get("title"), 255) or f"Course {course['id']}",
                "description": _brief(course, modules),
                "rqf_level": _level(course.get("rqf_level")),
                "subject_area": _clean(course.get("subject_area"), 255),
                "modules": len(modules),
            }
        )

    for entry in prepared:
        print(f"  {entry['code']:<14} {entry['title']}")
        print(
            f"  {'':<14} RQF {entry['rqf_level'] or '—'}"
            f"  ·  {entry['modules']} module(s)"
            f"  ·  description {len(entry['description'] or '')} chars"
        )

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    from app.db.session import session_scope
    from app.modules.quiz_configuration.models import Course, Quiz

    created_courses = updated_courses = created_quizzes = 0
    with session_scope() as session:
        for entry in prepared:
            course = session.query(Course).filter(Course.code == entry["code"]).one_or_none()
            if course is None:
                course = Course(code=entry["code"], title=entry["title"])
                session.add(course)
                created_courses += 1
            else:
                updated_courses += 1
            # Refreshed every run: their descriptions and levels change, and a stale brief produces
            # questions about a syllabus the course no longer has.
            course.title = entry["title"]
            course.description = entry["description"]
            course.rqf_level = entry["rqf_level"]
            course.subject_area = entry["subject_area"]
            session.flush()

            slug = f"{entry['code'].lower()}-check"
            if session.query(Quiz).filter(Quiz.slug == slug).one_or_none() is None:
                session.add(
                    Quiz(
                        course_id=course.id,
                        slug=slug,
                        title=f"{entry['title']} knowledge check"[:255],
                    )
                )
                created_quizzes += 1

    print(
        f"\ncreated {created_courses} course(s), refreshed {updated_courses}, "
        f"created {created_quizzes} quiz(zes)"
    )
    print("existing configuration versions were left untouched")
    print("\nnext: python -m scripts.generate_quiz_live --course LLP-<id> --count 20 --dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
