"""Store a sitting of a generated quiz: the answers, and the verdict.

Revision ID: b7e2c05d81af
Revises: a41f7c2b90de
Create Date: 2026-09-03

WHY
---
Marking previously computed a verdict and returned it. A pass that exists only in an HTTP response
is not a record of anything: "did this person pass, and on what" has to be answerable from a row
later, not from whoever still holds the response.

It is also what makes it possible to withhold the detail from the learner. The marking route now
returns the verdict and the score only — no per-question corrections — and the full detail lives
here for an administrator to read back.

WHAT IS NOT COPIED HERE
-----------------------
The correct answer. It lives on the question in UC-02's bank, which is where marking reads it from.
A second copy could disagree with the first, and the one that disagreed would be the one somebody
quoted.

The pass mark **is** copied, deliberately: a stored row reading 55% against a pass mark of 50 should
be understandable without a second lookup, and cannot be re-interpreted by a later change to
anything.

PORTABILITY
-----------
SQLite and PostgreSQL both. Named constraints throughout, since SQLite's ALTER goes through batch
mode and an unnamed constraint cannot be dropped by a downgrade. ``Boolean`` rather than a dialect
flag type, and no server-side timestamp default — ``submitted_at`` is written by the application
from its injected clock, which is what makes the timing testable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c05d81af"
down_revision: str | Sequence[str] | None = "a41f7c2b90de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qz_quiz_submissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("quiz_id", sa.String(length=36), nullable=False),
        sa.Column("learner_ref", sa.String(length=128), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("correct", sa.Integer(), nullable=False),
        sa.Column("percentage", sa.Float(), nullable=False),
        sa.Column("pass_mark", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_qz_quiz_submissions"),
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["qz_generated_quizzes.id"],
            name="fk_qz_quiz_submissions_quiz_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("total >= 0", name="total_non_negative"),
        # The arithmetic is constrained where it is stored, not only where it is computed: a row
        # claiming 30 correct out of 20 should be impossible to write.
        sa.CheckConstraint("correct >= 0 AND correct <= total", name="correct_within_total"),
        sa.CheckConstraint("percentage >= 0 AND percentage <= 100", name="percentage_range"),
        sa.CheckConstraint("pass_mark >= 0 AND pass_mark <= 100", name="pass_mark_range"),
    )
    op.create_index("ix_qz_quiz_submissions_quiz_id", "qz_quiz_submissions", ["quiz_id"])
    op.create_index(
        "ix_qz_quiz_submissions_learner_ref", "qz_quiz_submissions", ["learner_ref"]
    )

    op.create_table(
        "qz_submitted_answers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("question_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        # Nullable: a question left unanswered is stored as an answer of nothing, marked incorrect.
        # Recording the absence means a stored sitting accounts for every question that was asked.
        sa.Column("given_label", sa.String(length=4), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_qz_submitted_answers"),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["qz_quiz_submissions.id"],
            name="fk_qz_submitted_answers_submission_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        sa.UniqueConstraint("submission_id", "sequence", name="submission_id_sequence"),
    )
    op.create_index(
        "ix_qz_submitted_answers_question_id", "qz_submitted_answers", ["question_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_qz_submitted_answers_question_id", table_name="qz_submitted_answers")
    op.drop_table("qz_submitted_answers")
    op.drop_index("ix_qz_quiz_submissions_learner_ref", table_name="qz_quiz_submissions")
    op.drop_index("ix_qz_quiz_submissions_quiz_id", table_name="qz_quiz_submissions")
    op.drop_table("qz_quiz_submissions")
