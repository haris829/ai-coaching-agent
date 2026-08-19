"""quiz configuration schema, identity placeholder, attempt delivery positions

Adds UC-01 (Quiz Configuration & Rules) alongside the existing UC-02 question bank:

* ``qa_users``  — placeholder identity directory both capabilities resolve callers through
* ``qc_*``      — courses, quizzes, immutable configuration versions, attempts
* ``qb_question_usages.delivery_position`` — the locked position of a question within an attempt

Hand-adjusted after autogeneration, for two reasons that matter on a real server database:

1. ``qc_quizzes`` and ``qc_configuration_versions`` reference each other. Autogenerate inlined the
   forward reference into ``CREATE TABLE qc_quizzes``, which SQLite tolerates but PostgreSQL and
   SQL Server reject because the target table does not exist yet. The pointer is added as a
   separate constraint after both tables exist.
2. Autogenerate does not emit the integrity triggers, because they are attached to SQLAlchemy's
   ``after_create`` events rather than declared as table constraints. Without them a *migrated*
   database would silently lack the configuration-version immutability and attempt version-locking
   guarantees that a ``create_all`` database has. The DDL is imported from the models module, so
   there is exactly one definition of each trigger.

Revision ID: 998d713ed495
Revises: 5ea5d718773d
Create Date: 2026-08-18 10:52:50.592619
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.modules.quiz_configuration.models import (  # noqa: E402
    postgres_trigger_statements,
    sqlite_trigger_statements,
    trigger_names,
)

revision: str = "998d713ed495"
down_revision: Union[str, None] = "5ea5d718773d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Name of the deferred pointer from a quiz to its newest configuration version.
ACTIVE_VERSION_FK = "fk_qc_quizzes_active_configuration_version_id_qc_configuration_versions"


def _create_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        statements = sqlite_trigger_statements()
    elif dialect == "postgresql":
        statements = postgres_trigger_statements()
    else:
        # Another backend can still run the migration; the guarantees are also enforced by the
        # service layer, and the trigger DDL for that dialect belongs in its own revision.
        return
    for statement in statements:
        op.execute(statement)


def _drop_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        return
    for name in trigger_names():
        if dialect == "sqlite":
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
        else:
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON {_trigger_table(name)}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS fn_reject_configuration_version_update()")
        op.execute("DROP FUNCTION IF EXISTS fn_reject_attempt_version_change()")


def _trigger_table(trigger_name: str) -> str:
    from app.modules.quiz_configuration.models import ATTEMPT_LOCK_TRIGGER, IMMUTABLE_TABLES

    for table, name in IMMUTABLE_TABLES:
        if name == trigger_name:
            return table
    if trigger_name == ATTEMPT_LOCK_TRIGGER:
        return "qc_attempts"
    raise ValueError(f"unknown trigger {trigger_name}")


def upgrade() -> None:
    op.create_table(
        "qa_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("api_token", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'learner')", name=op.f("ck_qa_users_users_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_users")),
        sa.UniqueConstraint("api_token", name=op.f("uq_qa_users_api_token")),
        sa.UniqueConstraint("email", name=op.f("uq_qa_users_email")),
    )

    op.create_table(
        "qc_courses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qc_courses")),
        sa.UniqueConstraint("code", name=op.f("uq_qc_courses_code")),
    )

    # Created WITHOUT the active-version pointer; it is added once the versions table exists.
    op.create_table(
        "qc_quizzes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("active_configuration_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["qc_courses.id"],
            name=op.f("fk_qc_quizzes_course_id_qc_courses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qc_quizzes")),
        sa.UniqueConstraint("course_id", "slug", name="course_id_slug"),
    )
    with op.batch_alter_table("qc_quizzes", schema=None) as batch_op:
        batch_op.create_index("ix_qc_quizzes_course_id", ["course_id"], unique=False)

    op.create_table(
        "qc_configuration_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("time_limit_minutes", sa.Integer(), nullable=True),
        sa.Column("pass_mark", sa.Integer(), nullable=False),
        sa.Column("randomise_questions", sa.Boolean(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("delivery_mode", sa.String(length=32), nullable=False),
        sa.Column("settings_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "delivery_mode IN ('practice', 'assessment', 'exam')",
            name=op.f("ck_qc_configuration_versions_delivery_mode"),
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 50", name=op.f("ck_qc_configuration_versions_max_attempts")
        ),
        sa.CheckConstraint(
            "pass_mark BETWEEN 1 AND 100", name=op.f("ck_qc_configuration_versions_pass_mark")
        ),
        sa.CheckConstraint(
            "question_count BETWEEN 1 AND 100",
            name=op.f("ck_qc_configuration_versions_question_count"),
        ),
        sa.CheckConstraint(
            "time_limit_minutes IS NULL OR time_limit_minutes BETWEEN 1 AND 480",
            name=op.f("ck_qc_configuration_versions_time_limit"),
        ),
        sa.CheckConstraint(
            "version_number >= 1", name=op.f("ck_qc_configuration_versions_version_number")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["qa_users.id"],
            name=op.f("fk_qc_configuration_versions_created_by_user_id_qa_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["qc_quizzes.id"],
            name=op.f("fk_qc_configuration_versions_quiz_id_qc_quizzes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qc_configuration_versions")),
        sa.UniqueConstraint("quiz_id", "version_number", name="quiz_id_version_number"),
    )
    with op.batch_alter_table("qc_configuration_versions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_qc_configuration_versions_quiz_id", ["quiz_id", "version_number"], unique=False
        )

    # The forward pointer, now that both tables exist. RESTRICT: a version a quiz points at
    # cannot be deleted out from under it.
    with op.batch_alter_table("qc_quizzes", schema=None) as batch_op:
        batch_op.create_foreign_key(
            ACTIVE_VERSION_FK,
            "qc_configuration_versions",
            ["active_configuration_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "qc_configuration_version_question_types",
        sa.Column("configuration_version_id", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(length=32), nullable=False),
        sa.Column("question_quota", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "question_type IN ('SINGLE_CHOICE', 'TRUE_FALSE', 'MULTI_SELECT', 'SCENARIO', "
            "'DRAG_TO_ORDER')",
            name=op.f("ck_qc_configuration_version_question_types_question_type"),
        ),
        sa.CheckConstraint(
            "position >= 1", name=op.f("ck_qc_configuration_version_question_types_position")
        ),
        sa.CheckConstraint(
            "question_quota IS NULL OR question_quota >= 1",
            name=op.f("ck_qc_configuration_version_question_types_question_quota"),
        ),
        sa.ForeignKeyConstraint(
            ["configuration_version_id"],
            ["qc_configuration_versions.id"],
            name=op.f(
                "fk_qc_configuration_version_question_types_configuration_version_id_"
                "qc_configuration_versions"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "configuration_version_id",
            "question_type",
            name=op.f("pk_qc_configuration_version_question_types"),
        ),
    )

    op.create_table(
        "qc_configuration_version_topics",
        sa.Column("configuration_version_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.String(length=36), nullable=False),
        sa.Column("topic_slug", sa.String(length=96), nullable=False),
        sa.Column("topic_name", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 1", name=op.f("ck_qc_configuration_version_topics_position")
        ),
        sa.ForeignKeyConstraint(
            ["configuration_version_id"],
            ["qc_configuration_versions.id"],
            name=op.f(
                "fk_qc_configuration_version_topics_configuration_version_id_"
                "qc_configuration_versions"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "configuration_version_id", "topic_id", name=op.f("pk_qc_configuration_version_topics")
        ),
    )

    op.create_table(
        "qc_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("configuration_version_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("usage_ref", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score_percent", sa.Integer(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "status IN ('in_progress', 'submitted', 'abandoned')", name=op.f("ck_qc_attempts_status")
        ),
        sa.CheckConstraint("attempt_number >= 1", name=op.f("ck_qc_attempts_attempt_number")),
        sa.CheckConstraint(
            "score_percent IS NULL OR score_percent BETWEEN 0 AND 100",
            name=op.f("ck_qc_attempts_score_percent"),
        ),
        sa.ForeignKeyConstraint(
            ["configuration_version_id"],
            ["qc_configuration_versions.id"],
            name=op.f("fk_qc_attempts_configuration_version_id_qc_configuration_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["qc_quizzes.id"],
            name=op.f("fk_qc_attempts_quiz_id_qc_quizzes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["qa_users.id"],
            name=op.f("fk_qc_attempts_user_id_qa_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qc_attempts")),
        sa.UniqueConstraint(
            "quiz_id", "user_id", "attempt_number", name="quiz_id_user_id_attempt_number"
        ),
        sa.UniqueConstraint("usage_ref", name=op.f("uq_qc_attempts_usage_ref")),
    )
    with op.batch_alter_table("qc_attempts", schema=None) as batch_op:
        batch_op.create_index(
            "ix_qc_attempts_configuration_version_id",
            ["configuration_version_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_qc_attempts_learner", ["quiz_id", "user_id", "attempt_number"], unique=False
        )
        # One in-flight attempt per learner per quiz, enforced by the database.
        batch_op.create_index(
            "ix_qc_attempts_one_in_progress",
            ["quiz_id", "user_id"],
            unique=True,
            sqlite_where=sa.text("status = 'in_progress'"),
            postgresql_where=sa.text("status = 'in_progress'"),
        )

    # UC-02: the locked position of a question within an attempt.
    with op.batch_alter_table("qb_question_usages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("delivery_position", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_question_usages_attempt_ref_delivery_position",
            ["attempt_ref", "delivery_position"],
        )
        batch_op.create_check_constraint(
            "question_usages_delivery_position_positive",
            "delivery_position IS NULL OR delivery_position >= 1",
        )

    _create_triggers()


def downgrade() -> None:
    _drop_triggers()

    with op.batch_alter_table("qb_question_usages", schema=None) as batch_op:
        # The bare name, exactly as the upgrade and the model declare it: Alembic applies the
        # metadata naming convention itself, so passing the already-prefixed name asks it to drop
        # `ck_qb_question_usages_ck_qb_question_usages_…`, which never existed.
        batch_op.drop_constraint(
            "question_usages_delivery_position_positive", type_="check"
        )
        batch_op.drop_constraint(
            "uq_question_usages_attempt_ref_delivery_position", type_="unique"
        )
        batch_op.drop_column("delivery_position")

    with op.batch_alter_table("qc_attempts", schema=None) as batch_op:
        batch_op.drop_index(
            "ix_qc_attempts_one_in_progress",
            sqlite_where=sa.text("status = 'in_progress'"),
            postgresql_where=sa.text("status = 'in_progress'"),
        )
        batch_op.drop_index("ix_qc_attempts_learner")
        batch_op.drop_index("ix_qc_attempts_configuration_version_id")
    op.drop_table("qc_attempts")

    op.drop_table("qc_configuration_version_topics")
    op.drop_table("qc_configuration_version_question_types")

    # Release the forward pointer before the versions table goes.
    with op.batch_alter_table("qc_quizzes", schema=None) as batch_op:
        batch_op.drop_constraint(ACTIVE_VERSION_FK, type_="foreignkey")

    with op.batch_alter_table("qc_configuration_versions", schema=None) as batch_op:
        batch_op.drop_index("ix_qc_configuration_versions_quiz_id")
    op.drop_table("qc_configuration_versions")

    with op.batch_alter_table("qc_quizzes", schema=None) as batch_op:
        batch_op.drop_index("ix_qc_quizzes_course_id")
    op.drop_table("qc_quizzes")

    op.drop_table("qc_courses")
    op.drop_table("qa_users")
