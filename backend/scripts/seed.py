"""Seed a database so the whole workflow is demonstrable immediately.

Creates:

* four identities in the placeholder directory - one administrator, two learners and **one
  assessor** - with the learners enrolled on the course so UC-03 will let them start an attempt;
* one course and **three quizzes**, each there for a reason (see ``QUIZZES`` below);
* a question bank holding several questions of every one of the five types, sized so that a
  realistic 20-question configuration is satisfiable and a retake can draw a fresh paper.

    python -m scripts.seed

WHY THREE QUIZZES
-----------------
One is not enough to reach the system. A single unconfigured quiz demonstrates UC-01's versioning
and nothing else: a reviewer cannot sit anything until they have configured it themselves, and
UC-09 stays completely unreachable because no quiz is a formal assessment. So:

* **End of Course Assessment** - deliberately *unconfigured*, so the first administrator save
  produces version 1 and the immutable-versioning behaviour is visible from the start.
* **Practice Assessment** - pre-configured and immediately sittable, so the learner journey
  (deliver, autosave, submit, score, pass/fail, feedback, coaching, retake) can be walked without
  an administrator step first. Three attempts and a 60% pass mark, so passing, failing and retaking
  are all reachable.
* **Supervised Final Examination** - pre-configured as a **formal assessment**, which is the only
  way UC-09's conditions, identity confirmation, device session, disconnect handling and assessor
  approval can be exercised at all.

The two configured quizzes are configured **through UC-01's own service**, with the same validation,
the same bank-capacity check and the same immutable version write an administrator's save performs.
Nothing here inserts a configuration row directly; a seed that bypassed the rules would be seeding a
state the application cannot produce.

IDEMPOTENCE
-----------
Safe to re-run, and safe to run on every deploy: identities, the course and the quizzes are created
only when missing, the question bank rejects duplicate content, and a quiz that **already has an
active configuration version is never reconfigured** - a redeploy must not publish a new version
behind a reviewer's back, and must never disturb an attempt already locked to one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

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
from app.modules.quiz_configuration.context import build_context  # noqa: E402
from app.modules.quiz_configuration.models import Course, Quiz  # noqa: E402
from app.modules.quiz_configuration.services import configuration_service  # noqa: E402


def _token(variable: str, fallback: str) -> str:
    """A seeded identity's bearer token, from the environment where one is given.

    The fallbacks are fine locally and are **not** fine on a reachable deployment: ``admin-token``
    published in a git history is an administrator account for anyone who reads it. So each is
    overridable, and ``main`` refuses to seed the fallbacks into a deployed environment rather than
    trusting whoever wrote the deploy configuration to remember.
    """
    value = os.environ.get(variable, "").strip()
    return value or fallback


#: The placeholder directory's identities. Four, because three cannot reach UC-09: an assessor is a
#: distinct role that an administrator credential is deliberately refused for, and without one the
#: review-and-approve workflow - which decides whether a formal pass is ever certificated - cannot
#: be demonstrated at all.
SEED_USERS = [
    ("admin@example.com", "Course Admin", Role.ADMIN, _token("SEED_ADMIN_TOKEN", "admin-token")),
    (
        "learner@example.com",
        "Ada Learner",
        Role.LEARNER,
        _token("SEED_LEARNER_TOKEN", "learner-token"),
    ),
    (
        "learner2@example.com",
        "Ben Learner",
        Role.LEARNER,
        _token("SEED_LEARNER2_TOKEN", "learner2-token"),
    ),
    (
        "assessor@example.com",
        "Cara Assessor",
        Role.ASSESSOR,
        _token("SEED_ASSESSOR_TOKEN", "assessor-token"),
    ),
]

#: Which cohort each learner belongs to.
#:
#: Two learners in **two different** cohorts, on purpose. A cohort is a grouping within a course,
#: and UC-10's cohort filter is only demonstrable if the seeded population actually partitions:
#: with everyone in one cohort the filter looks like it does nothing, and with nobody in a cohort
#: it correctly returns the empty set — which reads to a reviewer as broken.
#:
#: The values match the two the integration tests use, so what a reviewer sees on a deployment and
#: what `verify_e2e` section 32 asserts are the same shape.
SEED_COHORTS = {
    "learner@example.com": "cohort-a",
    "learner2@example.com": "cohort-b",
}

#: Tokens that must never reach a deployed environment, mapped to the variable that overrides each,
#: so the refusal can name exactly what to set rather than describing the problem.
UNSAFE_DEFAULT_TOKENS = {
    "admin-token": "SEED_ADMIN_TOKEN",
    "learner-token": "SEED_LEARNER_TOKEN",
    "learner2-token": "SEED_LEARNER2_TOKEN",
    "assessor-token": "SEED_ASSESSOR_TOKEN",
}

COURSE_CODE = "SEC-101"
COURSE_TITLE = "Information Security Fundamentals"
QUIZ_SLUG = "end-of-course-assessment"
QUIZ_TITLE = "End of Course Assessment"

#: A paper a learner can sit straight away. Twelve questions across all five types, drawn from a
#: bank far larger, so a retake has room to be a genuinely different paper.
PRACTICE_CONFIGURATION: dict[str, Any] = {
    "questionCount": 12,
    "timeLimitMinutes": 30,
    "passMark": 60,
    "questionTypes": [
        {"type": "SINGLE_CHOICE", "quota": 4},
        {"type": "TRUE_FALSE", "quota": 2},
        {"type": "MULTI_SELECT", "quota": 2},
        {"type": "SCENARIO", "quota": 2},
        {"type": "DRAG_TO_ORDER", "quota": 2},
    ],
    "randomiseQuestions": True,
    "maxAttempts": 3,
    "deliveryMode": "assessment",
}

#: The same shape under UC-09's supervised rules. Shorter, because a reviewer walking the
#: conditions to identity to device to autosave to disconnect sequence should not also have to
#: answer twelve questions to reach the assessor's queue.
FORMAL_CONFIGURATION: dict[str, Any] = {
    "questionCount": 6,
    "timeLimitMinutes": 45,
    "passMark": 50,
    "questionTypes": [
        {"type": "SINGLE_CHOICE", "quota": 3},
        {"type": "TRUE_FALSE", "quota": 3},
    ],
    "randomiseQuestions": True,
    "maxAttempts": 2,
    "deliveryMode": "assessment",
    "isFormalAssessment": True,
    "requiresHumanReview": True,
    "requiresAssessorApproval": True,
}

#: ``(slug, title, configuration or None)``. ``None`` means "leave it unconfigured on purpose".
QUIZZES: list[tuple[str, str, dict[str, Any] | None]] = [
    (QUIZ_SLUG, QUIZ_TITLE, None),
    ("practice-assessment", "Practice Assessment", PRACTICE_CONFIGURATION),
    ("supervised-final-examination", "Supervised Final Examination", FORMAL_CONFIGURATION),
]

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


def seed_enrolments(db: Session, course_id: int) -> tuple[int, int]:
    """Enrol every learner on the course, each in a cohort. Returns ``(created, backfilled)``.

    UC-03 refuses to create an attempt for a learner who is not enrolled, so without this the seeded
    world would look configured but be unusable.

    THE COHORT, AND WHY THIS ALSO BACKFILLS
    ---------------------------------------
    UC-10 filters analytics by cohort, and it reads the cohort from **this row** rather than from a
    copy frozen onto the attempt. Two consequences, both deliberate:

    * a learner with no cohort matches no cohort filter — correct, and indistinguishable from a
      broken filter if *nobody* has one, which is exactly what an earlier version of this seed
      produced on a deployment;
    * setting the cohort here makes attempts that were sat **before** it was set filterable too,
      because the filter joins live. So a redeploy repairs the demonstration rather than only
      helping the next learner.

    The backfill is narrow on purpose: it fills a cohort that is ``NULL`` and never overwrites one
    that is set. A cohort somebody chose is not this script's to change.
    """
    created = 0
    backfilled = 0
    learners = db.scalars(select(User).where(User.role == Role.LEARNER.value)).all()
    for learner in learners:
        cohort = SEED_COHORTS.get(learner.email)
        key = (str(learner.id), str(course_id))
        existing = db.get(Enrolment, key)
        if existing is not None:
            if cohort and existing.cohort_id is None:
                existing.cohort_id = cohort
                backfilled += 1
            continue
        db.add(
            Enrolment(
                learner_id=str(learner.id),
                course_id=str(course_id),
                cohort_id=cohort,
                status=EnrolmentStatus.ACTIVE.value,
            )
        )
        created += 1
    db.flush()
    return created, backfilled


def seed_course_and_quizzes(db: Session) -> tuple[int, dict[str, int]]:
    """Create the course and the three quizzes, returning ``(course_id, {slug: quiz_id})``."""
    course = db.scalar(select(Course).where(Course.code == COURSE_CODE))
    if course is None:
        course = Course(code=COURSE_CODE, title=COURSE_TITLE)
        db.add(course)
        db.flush()

    quiz_ids: dict[str, int] = {}
    for slug, title, _configuration in QUIZZES:
        quiz = db.scalar(select(Quiz).where(Quiz.course_id == course.id, Quiz.slug == slug))
        if quiz is None:
            quiz = Quiz(course_id=course.id, slug=slug, title=title)
            db.add(quiz)
            db.flush()
        quiz_ids[slug] = quiz.id

    return course.id, quiz_ids


def configure_quizzes(quiz_ids: dict[str, int]) -> list[str]:
    """Publish version 1 for the quizzes that ship pre-configured.

    Goes through ``configuration_service.save_configuration`` - the same function the administrator
    endpoint calls - so the seeded state is one the application could have produced: authoritative
    field validation, topic-scope resolution, a real question-bank capacity check, and one immutable
    version written in one transaction.

    Runs after the question bank is stocked, because the capacity check is real: configuring a
    12-question paper against an empty bank is correctly refused.

    A quiz that already has an active version is left alone. Re-publishing on every deploy would
    create version 2, 3, 4 - each a real immutable version nobody asked for - and would move the
    quiz's active pointer out from under attempts a reviewer had already started.
    """
    configured: list[str] = []
    for slug, _title, configuration in QUIZZES:
        if configuration is None:
            continue
        quiz_id = quiz_ids[slug]

        with session_scope() as db:
            existing = db.get(Quiz, quiz_id)
            if existing is not None and existing.active_configuration_version_id is not None:
                print(f"  = {slug:<32} already configured (version left untouched)")
                continue

        # Its own session: save_configuration commits the version itself, and the read above must
        # not be holding a transaction open across it.
        with SessionLocal() as db:
            ctx = build_context(db)
            try:
                body, created = configuration_service.save_configuration(
                    ctx, quiz_id, configuration, actor_user_id=None, actor="seed"
                )
            except AppError as exc:
                # Reported rather than raised: an unconfigurable quiz is worth knowing about and is
                # not a reason to abandon a seed that has already stocked a usable bank.
                print(f"  ! {slug:<32} not configured - {exc.code}: {exc}")
                continue
            version = body.get("configuration", {}).get("versionNumber")
            print(f"  + {slug:<32} configured (version {version}, created={created})")
            configured.append(slug)

    return configured


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


def unsafe_default_tokens() -> list[str]:
    """The variables still carrying a published default token.

    Called before seeding a deployed environment. ``admin-token`` is in this repository's history,
    so seeding it into something with a public URL creates an administrator account whose credential
    anyone can read - and the failure is silent, which is what makes it worth refusing rather than
    warning about.
    """
    return sorted(
        {
            variable
            for _email, _name, _role, token in SEED_USERS
            for value, variable in UNSAFE_DEFAULT_TOKENS.items()
            if token == value
        }
    )


def seed(*, allow_default_tokens: bool | None = None) -> dict[str, Any]:
    """Seed the database and return a summary. Idempotent; safe on every deploy.

    ``allow_default_tokens`` defaults to "only in development". A deployed environment with an
    unset ``SEED_ADMIN_TOKEN`` raises rather than publishing a known credential.
    """
    from app.core.config import settings

    permitted = (not settings.is_production) if allow_default_tokens is None else allow_default_tokens
    if not permitted:
        unsafe = unsafe_default_tokens()
        if unsafe:
            raise RuntimeError(
                "Refusing to seed the built-in demo tokens into "
                f"ENVIRONMENT={settings.environment!r}: they are published in this repository. "
                f"Set {', '.join(unsafe)} to values you generated."
            )

    ensure_schema()

    with session_scope() as db:
        identities = seed_identities(db)
        course_id, quiz_ids = seed_course_and_quizzes(db)
        enrolments, cohorts_backfilled = seed_enrolments(db, course_id)

    # A separate session: the question service commits each question itself, so it must not share
    # the transaction that created the course and quizzes.
    with SessionLocal() as db:
        created, skipped = seed_question_bank(db)

    # After the bank, because the capacity check that guards a configuration is a real one.
    configured = configure_quizzes(quiz_ids)

    return {
        "identities": identities,
        "enrolments": enrolments,
        "cohortsBackfilled": cohorts_backfilled,
        "courseId": course_id,
        "quizIds": quiz_ids,
        "questionsCreated": created,
        "questionsAlreadyPresent": skipped,
        "quizzesConfigured": configured,
    }


def main() -> int:
    summary = seed()

    print(
        f"\nSeed complete."
        f"\n  identities:    {summary['identities']} created, {summary['enrolments']} enrolled"
        f"\n  cohorts:       {summary['cohortsBackfilled']} backfilled onto existing enrolments"
        f"\n  course:        {summary['courseId']}"
        f"\n  quizzes:       {summary['quizIds']}"
        f"\n  configured:    {summary['quizzesConfigured'] or 'none (already configured)'}"
        f"\n  question bank: {summary['questionsCreated']} created, "
        f"{summary['questionsAlreadyPresent']} already present"
    )
    print("\nIdentities for the test UI:")
    for email, display_name, role, token in SEED_USERS:
        print(f"  {role.value:<9} {display_name:<14} {email:<22} {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
