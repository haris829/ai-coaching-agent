"""Freeze each MCQ onto the quiz that asked it.

Revision ID: c3f81a4e2b70
Revises: b7e2c05d81af
Create Date: 2026-09-03

WHY
---
``qz_generated_quiz_questions`` held a reference into UC-02's question bank and nothing else, on
the reasoning that one copy of a question cannot disagree with another. That was the wrong way
round.

A bank question can be edited or retired. With only a reference, editing it silently rewrites every
quiz that ever used it and every sitting ever marked against it: a learner who passed in March could
be shown different questions in June, and the answer reported as correct for their submission could
be one that was not correct when they sat it. A stored result has to mean what it meant at the time.

UC-03 already freezes the questions an attempt delivered and UC-04 makes a confirmed result
immutable, for this exact reason. A generated quiz now gets the same treatment: the stem, the
options and the answer key are copied onto the row, and marking reads them from there.

``question_id`` stays as the link back to the bank — provenance, and the route to review or retire
the question. It is simply no longer the authority on what this quiz asked.

BACKFILL
--------
Existing rows are backfilled from the bank, which is the best available reconstruction: it is what
those quizzes would have reported a moment before this migration ran, so nothing changes meaning at
the point of upgrade. Rows whose question has since been deleted stay NULL and fall back to the
bank at read time, exactly as they did before.

The columns are nullable rather than backfilled-then-tightened. A NOT NULL would have to be enforced
against rows whose source question no longer exists, and failing an upgrade over history that cannot
be reconstructed is worse than a documented fallback.

PORTABILITY
-----------
SQLite and PostgreSQL both. ``op.batch_alter_table`` for the additions, since SQLite's ALTER goes
through batch mode. The backfill is one correlated UPDATE per column using only standard SQL — no
``json_object``, no ``FILTER``, no ``string_agg``, none of which exist on both. The options object
is assembled with plain concatenation for the same reason.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f81a4e2b70"
down_revision: str | Sequence[str] | None = "b7e2c05d81af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("qz_generated_quiz_questions") as batch:
        batch.add_column(sa.Column("question_text", sa.Text(), nullable=True))
        batch.add_column(sa.Column("options_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("answer_label", sa.String(length=4), nullable=True))

    connection = op.get_bind()

    # The stem, from the bank as it stands now.
    connection.execute(
        sa.text(
            """
            UPDATE qz_generated_quiz_questions
               SET question_text = (
                     SELECT q.question_text FROM qb_questions AS q
                      WHERE q.id = qz_generated_quiz_questions.question_id
                   )
             WHERE question_text IS NULL
            """
        )
    )

    # The key: the single option marked correct.
    connection.execute(
        sa.text(
            """
            UPDATE qz_generated_quiz_questions
               SET answer_label = (
                     SELECT o.label FROM qb_question_options AS o
                      WHERE o.question_id = qz_generated_quiz_questions.question_id
                        AND o.is_correct = 1
                   )
             WHERE answer_label IS NULL
            """
        )
    )

    # The options, as a JSON object. Assembled per row in Python rather than in SQL: every dialect
    # spells JSON aggregation differently, and getting the escaping right in raw SQL for text that
    # contains quotes is a worse risk than one extra pass over what is a small table.
    rows = connection.execute(
        sa.text(
            """
            SELECT g.id, o.label, o.text
              FROM qz_generated_quiz_questions AS g
              JOIN qb_question_options AS o ON o.question_id = g.question_id
             WHERE g.options_json IS NULL
             ORDER BY g.id, o.position
            """
        )
    ).all()

    import json

    grouped: dict[str, dict[str, str]] = {}
    for row_id, label, text in rows:
        grouped.setdefault(row_id, {})[label] = text
    for row_id, options in grouped.items():
        connection.execute(
            sa.text(
                "UPDATE qz_generated_quiz_questions SET options_json = :payload WHERE id = :id"
            ),
            {"payload": json.dumps(options), "id": row_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("qz_generated_quiz_questions") as batch:
        batch.drop_column("answer_label")
        batch.drop_column("options_json")
        batch.drop_column("question_text")
