"""Seed a local database so the whole workflow is demonstrable immediately.

Creates:

* three identities (one administrator, two learners) in the placeholder directory, with the two
  learners enrolled on the course so UC-03 will let them start an attempt;
* one course and one **deliberately unconfigured** quiz, so the first admin save produces
  version 1 and the versioning behaviour is visible from the start;
* a question bank holding several questions of every one of the five types, sized so that a
  realistic 20-question configuration is satisfiable.

Idempotent: identities and the course/quiz are created only when missing, and the question bank
rejects duplicate content, so re-running simply reports what already existed.

    python -m scripts.seed
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.errors import AppError  # noqa: E402
from app.db.metadata import target_metadata  # noqa: E402  (registers every table)
from app.db.session import SessionLocal, engine, session_scope  # noqa: E402
from app.modules.identity.enums import EnrolmentStatus  # noqa: E402
from app.modules.identity.models import Enrolment, User  # noqa: E402
from app.modules.identity.principal import Role  # noqa: E402
from app.modules.question_bank.csv_import.parser import ParsedRow  # noqa: E402
from app.modules.question_bank.csv_import.row_mapper import map_row  # noqa: E402
from app.modules.question_bank.csv_import.template import TEMPLATE_ROWS  # noqa: E402
from app.modules.question_bank.domain.validator import validate_question_draft  # noqa: E402
from app.modules.question_bank.services import question_service  # noqa: E402
from app.modules.quiz_configuration.models import Course, Quiz  # noqa: E402

#: Development credentials. Local only — the company's identity provider replaces them.
SEED_USERS = [
    ("admin@example.com", "Course Admin", Role.ADMIN, "admin-token"),
    ("learner@example.com", "Ada Learner", Role.LEARNER, "learner-token"),
    ("learner2@example.com", "Ben Learner", Role.LEARNER, "learner2-token"),
]

COURSE_CODE = "SEC-101"
COURSE_TITLE = "Information Security Fundamentals"
QUIZ_SLUG = "end-of-course-assessment"
QUIZ_TITLE = "End of Course Assessment"

#: How many questions of each template row to create. The template holds one worked example per
#: type; each copy varies its question text so it is a genuinely distinct question.
COPIES_PER_TYPE = 6


def ensure_schema() -> None:
    """Create any missing tables.

    Convenience for a first local run. A real deployment uses ``alembic upgrade head``, which is
    also what the migration-drift test checks.
    """
    target_metadata.create_all(bind=engine)


def seed_identities(db: Session) -> int:
    created = 0
    for email, display_name, role, token in SEED_USERS:
        if db.scalar(select(User).where(User.email == email)) is not None:
            continue
        db.add(
            User(
                email=email, display_name=display_name, role=role.value, api_token=token
            )
        )
        created += 1
    db.flush()
    return created


def seed_enrolments(db: Session, course_id: int) -> int:
    """Enrol every learner on the course.

    UC-03 refuses to create an attempt for a learner who is not enrolled, so without this the seeded
    world would look configured but be unusable.
    """
    created = 0
    learners = db.scalars(select(User).where(User.role == Role.LEARNER.value)).all()
    for learner in learners:
        key = (str(learner.id), str(course_id))
        if db.get(Enrolment, key) is not None:
            continue
        db.add(
            Enrolment(
                learner_id=str(learner.id),
                course_id=str(course_id),
                status=EnrolmentStatus.ACTIVE.value,
            )
        )
        created += 1
    db.flush()
    return created


def seed_course_and_quiz(db: Session) -> tuple[int, int]:
    course = db.scalar(select(Course).where(Course.code == COURSE_CODE))
    if course is None:
        course = Course(code=COURSE_CODE, title=COURSE_TITLE)
        db.add(course)
        db.flush()

    quiz = db.scalar(
        select(Quiz).where(Quiz.course_id == course.id, Quiz.slug == QUIZ_SLUG)
    )
    if quiz is None:
        # Left unconfigured on purpose: the first save an administrator makes becomes version 1.
        quiz = Quiz(course_id=course.id, slug=QUIZ_SLUG, title=QUIZ_TITLE)
        db.add(quiz)
        db.flush()

    return course.id, quiz.id


def seed_question_bank(db: Session) -> tuple[int, int]:
    created = 0
    skipped = 0

    for copy_number in range(1, COPIES_PER_TYPE + 1):
        for index, template_row in enumerate(TEMPLATE_ROWS):
            values = dict(template_row)
            if copy_number > 1:
                values["question_text"] = f"{values['question_text']} (variant {copy_number})"
                # external_ref is unique per question, so it has to vary with the copy.
                if values.get("external_ref"):
                    values["external_ref"] = f"{values['external_ref']}-{copy_number}"

            row = ParsedRow(row_number=index + 2, values=values, raw=dict(values))
            mapped = map_row(row)
            if mapped.draft is None or mapped.issues:
                print(f"  ! {values['type']}: {[issue.code for issue in mapped.issues]}")
                continue

            outcome = validate_question_draft(mapped.draft)
            if not outcome.ok:
                print(f"  ! {values['type']}: {[issue.code for issue in outcome.issues]}")
                continue

            try:
                question = question_service.create_question(
                    db, mapped.draft, actor="seed", commit=True
                )
            except AppError as exc:
                skipped += 1
                if exc.code != "DUPLICATE_QUESTION":
                    print(f"  · {values['type']:<14} skipped — {exc.code}")
                continue

            created += 1
            print(f"  + {question.reference}  {question.type}")

    return created, skipped


def main() -> int:
    ensure_schema()

    with session_scope() as db:
        identities = seed_identities(db)
        course_id, quiz_id = seed_course_and_quiz(db)
        enrolments = seed_enrolments(db, course_id)

    # A separate session: the question service commits each question itself, so it must not share
    # the transaction that created the course and quiz.
    with SessionLocal() as db:
        created, skipped = seed_question_bank(db)

    print(
        f"\nSeed complete."
        f"\n  identities:    {identities} created, {enrolments} enrolled"
        f"\n  course / quiz: course {course_id}, quiz {quiz_id} (unconfigured)"
        f"\n  question bank: {created} created, {skipped} already present"
        f"\n\nTokens for the test UI: admin-token, learner-token, learner2-token"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
