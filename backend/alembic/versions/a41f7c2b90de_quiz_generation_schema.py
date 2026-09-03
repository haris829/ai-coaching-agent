"""Generated quizzes, and the course columns that make good questions possible.

Revision ID: a41f7c2b90de
Revises: 7b41c0d9e5a2
Create Date: 2026-09-03

TWO UNRELATED-LOOKING CHANGES, ONE REASON
-----------------------------------------
The ``qz_`` tables remember which questions a generated quiz asked, so the marking call has
something to mark against.

The three columns added to ``qc_courses`` — ``description``, ``rqf_level``, ``subject_area`` — exist
because generation reads them. A course title alone produces generic questions; the same course with
a description and an RQF level produces questions pitched where the learner is actually being
assessed. All three are nullable and no business rule reads them, so every existing row and every
existing query is unaffected.

PORTABILITY
-----------
Written to run on both SQLite (local, tests) and PostgreSQL (Railway):

* ``String``/``Text``/``Integer``/``Float`` only — no dialect-specific types.
* Constraints are **named**, because SQLite's ALTER support goes through batch mode and an unnamed
  constraint cannot be dropped by a later downgrade.
* ``op.batch_alter_table`` for the ``qc_courses`` additions, since SQLite cannot ``ADD COLUMN``
  inside a plain ``ALTER TABLE`` for every case Alembic emits.
* No ``CURRENT_TIMESTAMP`` server defaults: ``created_at`` is written by the application from its
  injected clock, which is what makes time testable here at all.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a41f7c2b90de"
down_revision: str | Sequence[str] | None = "7b41c0d9e5a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- the course columns generation reads --------------------------------
    with op.batch_alter_table("qc_courses") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("rqf_level", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("subject_area", sa.String(length=255), nullable=True))

    # ---- one generated quiz -------------------------------------------------
    op.create_table(
        "qz_generated_quizzes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("course_ref", sa.String(length=64), nullable=True),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("pass_mark", sa.Float(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_qz_generated_quizzes"),
        sa.CheckConstraint("requested_count >= 1", name="requested_count_positive"),
        sa.CheckConstraint("question_count >= 0", name="question_count_non_negative"),
        sa.CheckConstraint("question_count <= requested_count", name="count_within_request"),
        # The pass mark is frozen per quiz, so the range is enforced where it is stored rather
        # than only where it is validated — a quiz marked against 250% should be impossible to
        # write, not merely impossible to request.
        sa.CheckConstraint("pass_mark >= 0 AND pass_mark <= 100", name="pass_mark_range"),
    )
    op.create_index(
        "ix_qz_generated_quizzes_course_ref", "qz_generated_quizzes", ["course_ref"]
    )

    # ---- the questions it asked, in order -----------------------------------
    op.create_table(
        "qz_generated_quiz_questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("quiz_id", sa.String(length=36), nullable=False),
        sa.Column("question_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_qz_generated_quiz_questions"),
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["qz_generated_quizzes.id"],
            name="fk_qz_generated_quiz_questions_quiz_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        # No two questions in one slot, and no question asked twice in one quiz. Both are database
        # rules rather than service rules because a duplicate would silently change what a
        # percentage means.
        sa.UniqueConstraint("quiz_id", "sequence", name="quiz_id_sequence"),
        sa.UniqueConstraint("quiz_id", "question_id", name="quiz_id_question_id"),
    )
    op.create_index(
        "ix_qz_generated_quiz_questions_question_id",
        "qz_generated_quiz_questions",
        ["question_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_qz_generated_quiz_questions_question_id",
        table_name="qz_generated_quiz_questions",
    )
    op.drop_table("qz_generated_quiz_questions")
    op.drop_index("ix_qz_generated_quizzes_course_ref", table_name="qz_generated_quizzes")
    op.drop_table("qz_generated_quizzes")

    with op.batch_alter_table("qc_courses") as batch:
        batch.drop_column("subject_area")
        batch.drop_column("rqf_level")
        batch.drop_column("description")
