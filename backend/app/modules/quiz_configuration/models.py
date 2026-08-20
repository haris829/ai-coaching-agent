"""SQLAlchemy models for UC-01 Quiz Configuration & Rules.

All tables are prefixed ``qc_`` so this schema merges into the larger Courses Quiz Agent
database without colliding with the question bank's ``qb_`` tables or with the company's own.

Integrity rules that must not depend on application logic alone are declared here:

* value ranges                → ``CheckConstraint``
* relationships               → ``ForeignKey``
* version / attempt numbering → ``UniqueConstraint``
* version immutability        → database triggers (SQLite and PostgreSQL)

Triggers are emitted per dialect, so the guarantees survive the move from the local SQLite file
to the company database.

This module deliberately declares **no question table and no attempt table**. The single question
bank is ``app.modules.question_bank``; the single owner of attempts is
``app.modules.attempt_delivery`` (UC-03). A configuration references question *types* and an
optional topic scope, and nothing more.

UC-01 held a provisional ``qc_attempts`` table before UC-03 existed, so that "start quiz" could be
demonstrated. UC-03's attempt model supersedes it completely — answers, revisions, flags, timing,
submission idempotency and pending state — so keeping both would have meant two competing records of
the same thing. UC-01 now reads the attempt *counts* it needs through
:class:`app.modules.quiz_configuration.ports.AttemptStatisticsPort`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DDL,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.question_types import QuestionPresentation
from app.db.base import Base, created_at_column, updated_at_column
from app.modules.quiz_configuration.domain.enums import DeliveryMode, QuestionType

TABLE_PREFIX = "qc_"

QUESTION_TYPE_VALUES = ", ".join(f"'{item.value}'" for item in QuestionType)
DELIVERY_MODE_VALUES = ", ".join(f"'{item.value}'" for item in DeliveryMode)
PRESENTATION_VALUES = ", ".join(f"'{item.value}'" for item in QuestionPresentation)


class Course(Base):
    """Placeholder course catalogue.

    The company system owns courses in production; this table exists so the capability runs
    end-to-end locally. Nothing in the business rules depends on its columns beyond identity and
    title, so replacing it is a repository change.
    """

    __tablename__ = f"{TABLE_PREFIX}courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    quizzes: Mapped[list[Quiz]] = relationship(back_populates="course")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Course {self.code}>"


class Quiz(Base):
    """A quiz that can be configured and, once configured, started."""

    __tablename__ = f"{TABLE_PREFIX}quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey(f"{TABLE_PREFIX}courses.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Pointer to the newest configuration version, set in the same transaction that creates
    #: that version — a quiz never points at a half-written version.
    active_configuration_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            f"{TABLE_PREFIX}configuration_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            # Named explicitly: the convention would compose
            # fk_qc_quizzes_active_configuration_version_id_qc_configuration_versions, which is 71
            # characters and PostgreSQL refuses anything over 63. See app/db/metadata.py.
            name="fk_qc_quizzes_active_version_id",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    course: Mapped[Course] = relationship(back_populates="quizzes")
    active_configuration_version: Mapped[ConfigurationVersion | None] = relationship(
        foreign_keys=[active_configuration_version_id], post_update=True
    )

    __table_args__ = (
        UniqueConstraint("course_id", "slug", name="course_id_slug"),
        Index(f"ix_{TABLE_PREFIX}quizzes_course_id", "course_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Quiz {self.slug}>"


class ConfigurationVersion(Base):
    """An immutable snapshot of every setting needed to run the quiz.

    Rows are never updated. A configuration change creates a new version and the quiz is
    repointed at it; historical versions stay exactly as they were, because attempts reference
    them forever. UC-03 depends on this.
    """

    __tablename__ = f"{TABLE_PREFIX}configuration_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey(f"{TABLE_PREFIX}quizzes.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    time_limit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pass_mark: Mapped[int] = mapped_column(Integer, nullable=False)
    randomise_questions: Mapped[bool] = mapped_column(nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_mode: Mapped[str] = mapped_column(String(32), nullable=False)

    # ---- delivery settings read by UC-03 ---------------------------------
    #: How the questions are presented (one at a time / all at once). Separate from
    #: ``delivery_mode``, which is about grading and feedback.
    #
    #: All three carry a ``server_default`` as well as a Python default. That is what lets the
    #: migration add them to a table that already has rows, and it keeps a direct SQL write valid.
    question_presentation: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=QuestionPresentation.ALL_AT_ONCE.value,
        server_default=QuestionPresentation.ALL_AT_ONCE.value,
    )
    #: Shuffle options/items within a question, independently of question order.
    randomise_option_order: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    #: May the learner submit with questions left unanswered?
    allow_incomplete_submission: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=true()
    )

    # ---- formal assessment settings read by UC-09 ------------------------
    #: Whether sitting this quiz is a **formal assessment**: exam conditions acknowledged, identity
    #: confirmed, one device, no pause, no coaching while it runs, and no certificate until a human
    #: assessor approves the pass.
    #:
    #: It lives on the immutable version, not on the quiz, for the reason every other delivery
    #: setting does: UC-09 resolves it from the version *locked to the attempt*, so a quiz made
    #: formal tomorrow cannot retroactively change what a learner sat today — and one made informal
    #: tomorrow cannot release a certificate that is still waiting on an assessor.
    #:
    #: ``False`` is exactly the system as it was before UC-09, which is why the three columns are
    #: defaulted on both sides: every existing version reads as a standard quiz.
    is_formal_assessment: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    #: Whether a passing formal attempt goes to a human assessor at all. Consulted **only** when
    #: ``is_formal_assessment``; meaningless otherwise, and UC-09 never reads it otherwise.
    requires_human_review: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=true()
    )
    #: Whether that assessor's approval is required before the certificate workflow may run.
    #: Defaulted to ``True`` deliberately: a formal assessment configured carelessly withholds a
    #: certificate rather than issuing one nobody checked.
    requires_assessor_approval: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=true()
    )

    #: Canonical hash of the settings; lets the service detect a no-op re-save.
    settings_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("qa_users.id", ondelete="RESTRICT"), nullable=True
    )
    #: Audit label, resolved from the identity seam. Survives the placeholder user table.
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    quiz: Mapped[Quiz] = relationship(foreign_keys=[quiz_id])
    question_types: Mapped[list[ConfigurationVersionQuestionType]] = relationship(
        back_populates="configuration_version",
        cascade="all, delete-orphan",
        order_by="ConfigurationVersionQuestionType.position",
        lazy="selectin",
    )
    topics: Mapped[list[ConfigurationVersionTopic]] = relationship(
        back_populates="configuration_version",
        cascade="all, delete-orphan",
        order_by="ConfigurationVersionTopic.position",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("quiz_id", "version_number", name="quiz_id_version_number"),
        CheckConstraint("version_number >= 1", name="version_number"),
        CheckConstraint("question_count BETWEEN 1 AND 100", name="question_count"),
        CheckConstraint(
            "time_limit_minutes IS NULL OR time_limit_minutes BETWEEN 1 AND 480",
            name="time_limit",
        ),
        CheckConstraint("pass_mark BETWEEN 1 AND 100", name="pass_mark"),
        CheckConstraint("max_attempts BETWEEN 1 AND 50", name="max_attempts"),
        CheckConstraint(f"delivery_mode IN ({DELIVERY_MODE_VALUES})", name="delivery_mode"),
        CheckConstraint(
            f"question_presentation IN ({PRESENTATION_VALUES})", name="question_presentation"
        ),
        Index(f"ix_{TABLE_PREFIX}configuration_versions_quiz_id", "quiz_id", "version_number"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ConfigurationVersion quiz={self.quiz_id} v{self.version_number}>"


class ConfigurationVersionQuestionType(Base):
    """One selected question type on an immutable configuration version."""

    __tablename__ = f"{TABLE_PREFIX}configuration_version_question_types"

    configuration_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            f"{TABLE_PREFIX}configuration_versions.id",
            ondelete="CASCADE",
            # 93 characters under the convention — the longest in the schema, and well past
            # PostgreSQL's 63-character limit.
            name="fk_qc_version_question_types_version_id",
        ),
        primary_key=True,
    )
    question_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: NULL = no per-type quota; questions are drawn freely across the selected types.
    question_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    configuration_version: Mapped[ConfigurationVersion] = relationship(
        back_populates="question_types"
    )

    __table_args__ = (
        CheckConstraint(f"question_type IN ({QUESTION_TYPE_VALUES})", name="question_type"),
        CheckConstraint(
            "question_quota IS NULL OR question_quota >= 1", name="question_quota"
        ),
        CheckConstraint("position >= 1", name="position"),
    )


class ConfigurationVersionTopic(Base):
    """Optional topic scope frozen onto a configuration version.

    ``topic_id`` references the question bank's topic, but deliberately **without** a foreign
    key, and the topic's name/slug are frozen alongside it. This mirrors how the question bank
    freezes topic names into question snapshots: a version must keep meaning exactly what it
    meant when it was written, so renaming or deleting a topic later cannot rewrite history.
    """

    __tablename__ = f"{TABLE_PREFIX}configuration_version_topics"

    configuration_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            f"{TABLE_PREFIX}configuration_versions.id",
            ondelete="CASCADE",
            name="fk_qc_version_topics_version_id",
        ),
        primary_key=True,
    )
    topic_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: Frozen at save time, for display in historical versions.
    topic_slug: Mapped[str] = mapped_column(String(96), nullable=False)
    topic_name: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    configuration_version: Mapped[ConfigurationVersion] = relationship(back_populates="topics")

    __table_args__ = (CheckConstraint("position >= 1", name="position"),)


# ---------------------------------------------------------------------------
# Integrity triggers
#
# The two UC-01 guarantees that application code must not be the only thing protecting: a
# configuration version can never be edited once written, and an attempt can never be repointed
# at a different version.
# ---------------------------------------------------------------------------

IMMUTABLE_MESSAGE = (
    "IMMUTABLE_CONFIGURATION_VERSION: configuration versions cannot be modified; "
    "create a new version instead"
)

_VERSIONS = f"{TABLE_PREFIX}configuration_versions"
_VERSION_TYPES = f"{TABLE_PREFIX}configuration_version_question_types"
_VERSION_TOPICS = f"{TABLE_PREFIX}configuration_version_topics"

POSTGRES_IMMUTABLE_FN = f"""
CREATE OR REPLACE FUNCTION fn_reject_configuration_version_update()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '{IMMUTABLE_MESSAGE}';
END;
$$ LANGUAGE plpgsql;
"""



#: The frozen tables, and the trigger that protects each. Declared once and consumed by both the
#: ``create_all`` hooks below and the Alembic migration, so a migrated database and a
#: ``create_all`` database cannot end up with different guarantees.
IMMUTABLE_TABLES: tuple[tuple[str, str], ...] = (
    (_VERSIONS, "trg_qc_config_version_no_update"),
    (_VERSION_TYPES, "trg_qc_config_version_types_no_update"),
    (_VERSION_TOPICS, "trg_qc_config_version_topics_no_update"),
)

def _sqlite_immutable(table: str, trigger: str) -> str:
    return f"""
CREATE TRIGGER {trigger}
BEFORE UPDATE ON {table}
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_MESSAGE}');
END;
"""


def _postgres_immutable(table: str, trigger: str) -> str:
    return f"""
CREATE TRIGGER {trigger}
BEFORE UPDATE ON {table}
FOR EACH ROW EXECUTE FUNCTION fn_reject_configuration_version_update();
"""


def sqlite_trigger_statements() -> list[str]:
    """SQLite DDL for the UC-01 immutability triggers. Used by the Alembic migration."""
    return [_sqlite_immutable(table, trigger) for table, trigger in IMMUTABLE_TABLES]


def postgres_trigger_statements() -> list[str]:
    """PostgreSQL DDL for the same guarantees, functions first."""
    return [
        POSTGRES_IMMUTABLE_FN,
        *(_postgres_immutable(table, trigger) for table, trigger in IMMUTABLE_TABLES),
    ]


def trigger_names() -> list[str]:
    return [trigger for _table, trigger in IMMUTABLE_TABLES]


def _attach_triggers() -> None:
    """Emit each trigger right after the table it protects is created.

    Per-table rather than one batch at the end, so the target table always exists by the time the
    DDL runs regardless of how ``create_all`` orders them.
    """
    protected: list[tuple[str, str]] = [
        (table, _sqlite_immutable(table, trigger)) for table, trigger in IMMUTABLE_TABLES
    ]

    postgres_by_table: dict[str, list[str]] = {
        table: [POSTGRES_IMMUTABLE_FN, _postgres_immutable(table, trigger)]
        for table, trigger in IMMUTABLE_TABLES
    }

    for table_name, sqlite_sql in protected:
        table = Base.metadata.tables[table_name]
        event.listen(table, "after_create", DDL(sqlite_sql).execute_if(dialect="sqlite"))
        for statement in postgres_by_table[table_name]:
            event.listen(
                table, "after_create", DDL(statement).execute_if(dialect="postgresql")
            )


_attach_triggers()


def is_immutability_violation(error: BaseException) -> bool:
    """True when a database trigger rejected an edit to frozen configuration data."""
    return "IMMUTABLE_CONFIGURATION_VERSION" in str(error)
