"""Copy the course catalogue from one of our databases into another.

    python -m scripts.copy_courses_between_databases --from sqlite:///./quiz_agent.db --to "postgresql+psycopg://..."

WHY THIS EXISTS
---------------
The catalogue is imported from the company's systems by ``import_company_courses`` and
``import_platform_courses``, both of which need credentials for *their* databases. When those are
not to hand — a local PostgreSQL for a demo, a fresh review deployment — this moves the courses
already imported into one of our databases across to another, so a picker has something real in it
rather than the single seeded placeholder.

Courses only. Not quizzes, not configurations, not questions, and certainly not attempts or
results: those are records of things that happened in one database and mean nothing in another.
A course is reference data, which is the only kind of row it is safe to copy.

Idempotent on the course code. Existing rows are refreshed rather than duplicated, and the target's
own primary keys are left to it — copying integer ids across would desynchronise its sequence and
make the next insert fail on a duplicate key.
"""

from __future__ import annotations

import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

FIELDS = ("code", "title", "description", "rqf_level", "subject_area")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", required=True, help="source DATABASE_URL")
    parser.add_argument("--to", dest="target", required=True, help="target DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.core.config import Settings
    from app.modules.quiz_configuration.models import Course

    def normalised(url: str) -> str:
        """The URL as the application itself would read it.

        Routed through ``Settings`` rather than reimplemented, because the ``postgres://`` and
        ``postgresql://`` rewrites live in one field validator there and a second copy here would
        eventually disagree with it. A managed provider's URL can therefore be pasted unchanged.
        """
        return Settings(database_url=url).database_url

    source_url = normalised(args.source)
    target_url = normalised(args.target)
    if source_url == target_url:
        sys.exit("source and target are the same database")

    source = create_engine(source_url)
    target = create_engine(target_url)

    with Session(source) as read:
        rows = [
            {field: getattr(course, field, None) for field in FIELDS}
            for course in read.scalars(select(Course).order_by(Course.code)).all()
        ]

    print(f"read {len(rows)} course(s) from the source\n")
    for row in rows[:8]:
        brief = "has description" if row["description"] else "title only"
        print(f"  {row['code']:<12} {str(row['title'])[:56]:<58}{brief}")
    if len(rows) > 8:
        print(f"  … and {len(rows) - 8} more")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    created = refreshed = 0
    with Session(target) as write:
        for row in rows:
            course = write.scalars(
                select(Course).where(Course.code == row["code"])
            ).one_or_none()
            if course is None:
                # No id: the target assigns its own, so its sequence stays in step.
                course = Course(code=row["code"], title=row["title"])
                write.add(course)
                created += 1
            else:
                refreshed += 1
            for field in FIELDS:
                setattr(course, field, row[field])
        write.commit()

    print(f"\ncreated {created}, refreshed {refreshed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
