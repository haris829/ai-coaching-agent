"""This package's own tables: ``qgen_runs``, ``qgen_questions``, ``qgen_question_options``.

Three things this module exists to get right.

**The question is frozen.** The stem, the four options and the correct label are written onto
these rows. Nothing here stores a reference to something editable elsewhere. If a question could
be edited after the fact, every past result would silently change meaning: somebody who passed in
March could be shown different questions in June, and the answer recorded as correct for their
submission might not be what was correct when they sat it.

**The answer key stays server-side.** :func:`learner_view` exists so that "the question, without
the key" is a thing the code can hand out, rather than something each caller has to remember to
strip. ``is_correct`` and ``answer_label`` appear only in :func:`admin_view`.

**A generated question is not approved content.** Every row is written ``DRAFT``. A model can
produce a question that is fluent, plausible and wrong, and the learner who fails on it is being
certified against a mistake nobody read. Nothing in this package writes any other status; the
column allows the others so that whoever builds the review has somewhere to put the result.

Nothing here writes to ``qc_courses``, or to the sibling project's ``qb_*`` or ``qz_*`` tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

from .domain.generation import (
    MAX_AVOID_STEMS,
    CourseBrief,
    GeneratedOption,
    GeneratedQuestion,
    stem_key,
)

#: Written verbatim by :func:`ensure_schema`. ``IF NOT EXISTS`` throughout, so running it twice
#: is a no-op and it can be run before every command without a migration tool.
SCHEMA = """
CREATE TABLE IF NOT EXISTS qgen_runs (
    id            bigserial PRIMARY KEY,
    course_ref    text        NOT NULL,
    course_code   text,
    course_title  text        NOT NULL,
    grounding     text        NOT NULL,
    requested     integer     NOT NULL,
    asked_for     integer     NOT NULL,
    stored        integer     NOT NULL,
    refused       integer     NOT NULL,
    refusals      text,
    model         text        NOT NULL,
    status        text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qgen_questions (
    id            bigserial   PRIMARY KEY,
    run_id        bigint      NOT NULL REFERENCES qgen_runs (id) ON DELETE CASCADE,
    course_code   text,
    course_ref    text        NOT NULL,
    stem_key      text        NOT NULL,
    question_text text        NOT NULL,
    answer_label  text        NOT NULL CHECK (answer_label IN ('A', 'B', 'C', 'D')),
    explanation   text,
    grounding     text        NOT NULL,
    status        text        NOT NULL DEFAULT 'DRAFT'
                  CHECK (status IN ('DRAFT', 'APPROVED', 'RETIRED')),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qgen_question_options (
    id          bigserial PRIMARY KEY,
    question_id bigint    NOT NULL REFERENCES qgen_questions (id) ON DELETE CASCADE,
    label       text      NOT NULL CHECK (label IN ('A', 'B', 'C', 'D')),
    option_text text      NOT NULL,
    is_correct  boolean   NOT NULL,
    UNIQUE (question_id, label)
);

-- History is always read newest-first for one course, which is exactly this index.
CREATE INDEX IF NOT EXISTS qgen_questions_by_code
    ON qgen_questions (course_code, id DESC);
-- The same read, for a reference that matched no course.
CREATE INDEX IF NOT EXISTS qgen_questions_by_ref
    ON qgen_questions (lower(course_ref), id DESC);
"""


@dataclass(frozen=True, slots=True)
class StoredQuestion:
    """A question as it now exists in the database."""

    id: int
    run_id: int
    course_code: str | None
    course_ref: str
    question_text: str
    options: tuple[GeneratedOption, ...]
    answer_label: str
    explanation: str | None
    grounding: str
    status: str
    created_at: datetime


def ensure_schema(conn: psycopg.Connection) -> None:
    """Create the three tables if they are not there. Safe to call repeatedly."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA)


def previously_asked(
    conn: psycopg.Connection,
    brief: CourseBrief,
    *,
    limit: int = MAX_AVOID_STEMS,
) -> tuple[str, ...]:
    """The stems this course has already been asked, newest first.

    Capped, because the list goes into the prompt and an uncapped one would eventually leave no
    room for the instruction it is attached to.

    Matched on the course code where one resolved, and on the caller's reference where none did -
    otherwise every unmatched request would share one bucket, and two different unmatched
    subjects would each be told to avoid the other's questions.

    Only this package's own questions are read. The sibling project's 676 generated questions
    live in tables this package does not touch; reading them would couple two schemas together
    for a benefit that disappears the moment either one changes.
    """
    with conn.cursor() as cur:
        if brief.code:
            cur.execute(
                "SELECT question_text FROM qgen_questions "
                "WHERE course_code = %s ORDER BY id DESC LIMIT %s",
                (brief.code, limit),
            )
        else:
            cur.execute(
                "SELECT question_text FROM qgen_questions "
                "WHERE course_code IS NULL AND lower(course_ref) = lower(%s) "
                "ORDER BY id DESC LIMIT %s",
                (brief.name, limit),
            )
        return tuple(record["question_text"] for record in cur.fetchall())


def open_run(
    conn: psycopg.Connection,
    *,
    course_ref: str,
    brief: CourseBrief,
    requested: int,
    asked_for: int,
    model: str,
) -> int:
    """Start a run row and return its id. Counts are filled in by :func:`close_run`."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO qgen_runs "
            "(course_ref, course_code, course_title, grounding, requested, asked_for, "
            " stored, refused, model, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 0, 0, %s, 'RUNNING') RETURNING id",
            (course_ref, brief.code, brief.name, brief.grounding, requested, asked_for, model),
        )
        return int(cur.fetchone()["id"])


def close_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    stored: int,
    refused: int,
    refusals: tuple[tuple[str, int], ...],
    status: str,
) -> None:
    """Record what the run actually produced.

    ``refusals`` is flattened to text in Python rather than by a JSON function in SQL. PostgreSQL
    and SQLite disagree about which of those functions exist and what they are called, and this
    is not worth a portability problem.
    """
    summary = "; ".join(f"{reason} x{count}" for reason, count in refusals) or None
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE qgen_runs SET stored = %s, refused = %s, refusals = %s, status = %s "
            "WHERE id = %s",
            (stored, refused, summary, status, run_id),
        )


def store_questions(
    conn: psycopg.Connection,
    *,
    run_id: int,
    course_ref: str,
    brief: CourseBrief,
    questions: tuple[GeneratedQuestion, ...],
) -> list[int]:
    """Write the questions, frozen, as drafts. Returns the new question ids."""
    ids: list[int] = []
    with conn.cursor() as cur:
        for question in questions:
            cur.execute(
                "INSERT INTO qgen_questions "
                "(run_id, course_code, course_ref, stem_key, question_text, answer_label, "
                " explanation, grounding, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'DRAFT') RETURNING id",
                (
                    run_id,
                    brief.code,
                    course_ref,
                    question.key,
                    question.question_text,
                    question.answer_label,
                    question.explanation,
                    brief.grounding,
                ),
            )
            question_id = int(cur.fetchone()["id"])
            ids.append(question_id)
            cur.executemany(
                "INSERT INTO qgen_question_options "
                "(question_id, label, option_text, is_correct) VALUES (%s, %s, %s, %s)",
                [
                    (question_id, option.label, option.text, option.is_correct)
                    for option in question.options
                ],
            )
    return ids


def read_questions(conn: psycopg.Connection, question_ids: list[int]) -> list[StoredQuestion]:
    """Read questions back by id, in the order asked for.

    Two queries and a join in Python rather than one query with ``json_agg``. Assembling JSON in
    the database is the shortest version and the least portable one.
    """
    if not question_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, run_id, course_code, course_ref, question_text, answer_label, "
            "       explanation, grounding, status, created_at "
            "FROM qgen_questions WHERE id = ANY(%s)",
            (question_ids,),
        )
        rows = {int(record["id"]): record for record in cur.fetchall()}

        cur.execute(
            "SELECT question_id, label, option_text, is_correct FROM qgen_question_options "
            "WHERE question_id = ANY(%s) ORDER BY question_id, label",
            (question_ids,),
        )
        options: dict[int, list[GeneratedOption]] = {}
        for record in cur.fetchall():
            options.setdefault(int(record["question_id"]), []).append(
                GeneratedOption(
                    label=record["label"],
                    text=record["option_text"],
                    # Read as the boolean it is. Never `is_correct = 1`: SQLite accepts that
                    # silently and PostgreSQL refuses it outright.
                    is_correct=bool(record["is_correct"]),
                )
            )

    stored: list[StoredQuestion] = []
    for question_id in question_ids:
        record = rows.get(question_id)
        if record is None:
            continue
        stored.append(
            StoredQuestion(
                id=question_id,
                run_id=int(record["run_id"]),
                course_code=record["course_code"],
                course_ref=record["course_ref"],
                question_text=record["question_text"],
                options=tuple(options.get(question_id, [])),
                answer_label=record["answer_label"],
                explanation=record["explanation"],
                grounding=record["grounding"],
                status=record["status"],
                created_at=record["created_at"],
            )
        )
    return stored


def questions_for_course(
    conn: psycopg.Connection,
    *,
    code: str | None = None,
    ref: str | None = None,
    limit: int = 20,
) -> list[StoredQuestion]:
    """Questions already held for a course, newest first.

    So that looking at what a topic already has costs a database read rather than a model call.
    Matched the same way :func:`previously_asked` matches: by code where the course resolved, and
    by the caller's own reference where it did not.
    """
    with conn.cursor() as cur:
        if code:
            cur.execute(
                "SELECT id FROM qgen_questions WHERE course_code = %s ORDER BY id DESC LIMIT %s",
                (code, limit),
            )
        elif ref:
            cur.execute(
                "SELECT id FROM qgen_questions "
                "WHERE course_code IS NULL AND lower(course_ref) = lower(%s) "
                "ORDER BY id DESC LIMIT %s",
                (ref, limit),
            )
        else:
            return []
        ids = [int(record["id"]) for record in cur.fetchall()]
    return read_questions(conn, ids)


def count_for_course(conn: psycopg.Connection, code: str) -> int:
    """How many questions this package holds for a course code."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM qgen_questions WHERE course_code = %s", (code,))
        return int(cur.fetchone()["n"])


# ---------------------------------------------------------------------------
# The two views of a stored question
# ---------------------------------------------------------------------------


def learner_view(question: StoredQuestion) -> dict:
    """The question as a learner may see it: no key, anywhere.

    A single function rather than a rule each caller applies, because "remember to remove the
    answer" is a rule that holds until the day somebody adds a field.
    """
    return {
        "id": question.id,
        "question": question.question_text,
        "options": [
            {"label": option.label, "text": option.text} for option in question.options
        ],
    }


def admin_view(question: StoredQuestion) -> dict:
    """The whole question, including the key and its draft status. Administrators only."""
    return {
        "id": question.id,
        "run_id": question.run_id,
        "course_code": question.course_code,
        "course_ref": question.course_ref,
        "question": question.question_text,
        "options": [
            {"label": option.label, "text": option.text, "is_correct": option.is_correct}
            for option in question.options
        ],
        "answer": question.answer_label,
        "explanation": question.explanation,
        "grounding": question.grounding,
        "status": question.status,
        "created_at": question.created_at.isoformat(),
    }


def history_keys(stems: tuple[str, ...]) -> frozenset[str]:
    """History as the parser wants it: normalised keys, for exact-repeat detection."""
    return frozenset(stem_key(stem) for stem in stems)
