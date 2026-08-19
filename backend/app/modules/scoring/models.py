"""SQLAlchemy ORM models for UC-04 Answer Validation & Scoring.

Tables are prefixed ``qr_`` (quiz *results*), so this schema sits alongside UC-01's ``qc_``,
UC-02's ``qb_``, UC-03's ``qd_`` and UC-05's ``qg_`` tables without collision.

The invariants UC-04 must hold are expressed as database constraints rather than left to application
code:

* ``uq_qr_attempt_results_attempt_id`` -- **one result per attempt**. This is what makes scoring
  idempotent under a race: a second concurrent scoring run loses the insert and adopts the winner's
  row rather than producing a second score.
* ``trg_qr_result_immutable_when_scored`` -- a trigger that rejects any ``UPDATE`` to a result once
  it has reached ``SCORED``. "Confirmed scores are immutable" is then a property of the database,
  not a rule every future caller has to remember.
* ``trg_qr_question_score_no_update`` -- per-question scores are written once and never edited.
* ``ck_qr_attempt_results_scored_state`` -- a scored result always carries the instant it was
  scored, and a pending one never does.

Cross-boundary references (``attempt_id``, ``learner_id``, ``quiz_id``, ``question_id``,
``configuration_version_id``) are deliberately *soft*: indexed columns without foreign keys. Those
rows belong to UC-03, UC-02 and the platform, and a result must survive them being superseded --
exactly as UC-03's own schema does, and for the same reason. Everything inside UC-04's aggregate
(``qr_question_scores`` -> ``qr_attempt_results``) is bound by a real foreign key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, id_column
from app.modules.scoring.domain.enums import QuestionOutcome, ResultStatus

TABLE_PREFIX = "qr_"

_RESULT_STATUS_VALUES = ", ".join(f"'{item.value}'" for item in ResultStatus)
_OUTCOME_VALUES = ", ".join(f"'{item.value}'" for item in QuestionOutcome)


class AttemptResult(Base):
    """The scored result of one submitted attempt.

    Carries the pass mark of the attempt's *own* configuration version, copied at scoring time, so
    UC-05 gates on the rules the learner actually sat under even after the quiz is reconfigured.
    """

    __tablename__ = f"{TABLE_PREFIX}attempt_results"

    id: Mapped[str] = id_column()

    #: Soft reference to ``qd_attempts.id``. Unique: one result per attempt, forever.
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    #: Soft reference to the successful ``qd_attempt_submissions`` row, when there is one.
    submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The configuration version the attempt was locked to -- never the quiz's latest.
    configuration_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Frozen from that version's snapshot, so UC-05 never has to re-resolve it.
    pass_mark_percentage: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ResultStatus.PENDING_SCORE.value
    )

    total_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    maximum_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incorrect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unanswered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: From UC-03's server-authoritative stamps. NULL only if the attempt had no submission time.
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    time_taken_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Data defects that blocked confirmation: ``[{code, questionId?, position?}]``.
    anomalies: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: How many scoring runs this result has seen. A retry increments it rather than adding a row.
    scoring_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Which version of the scoring rules produced it.
    algorithm_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    scored_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    question_scores: Mapped[list[QuestionScoreRow]] = relationship(
        back_populates="result",
        cascade="all, delete-orphan",
        order_by="QuestionScoreRow.position",
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_RESULT_STATUS_VALUES})", name="status"),
        CheckConstraint("total_marks >= 0 AND maximum_marks >= 0", name="marks_non_negative"),
        CheckConstraint("total_marks <= maximum_marks", name="marks_within_maximum"),
        CheckConstraint("percentage >= 0 AND percentage <= 100", name="percentage_range"),
        CheckConstraint(
            "pass_mark_percentage >= 0 AND pass_mark_percentage <= 100", name="pass_mark_range"
        ),
        CheckConstraint(
            "total_questions >= 0 AND correct_count >= 0 AND incorrect_count >= 0 "
            "AND unanswered_count >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint(
            "time_taken_seconds IS NULL OR time_taken_seconds >= 0", name="time_taken_non_negative"
        ),
        CheckConstraint("scoring_attempt_count >= 0", name="scoring_attempt_count_non_negative"),
        # A scored result always says when; a pending one never does.
        CheckConstraint("(status = 'SCORED') = (scored_at IS NOT NULL)", name="scored_state"),
        Index(f"ix_{TABLE_PREFIX}attempt_results_learner_quiz", "learner_id", "quiz_id"),
        Index(f"ix_{TABLE_PREFIX}attempt_results_status", "status"),
        Index(
            f"ix_{TABLE_PREFIX}attempt_results_configuration_version_id",
            "configuration_version_id",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AttemptResult attempt={self.attempt_id} {self.status} {self.percentage}%>"


class QuestionScoreRow(Base):
    """One question's marks within a result. Written once, never edited.

    Carries a frozen copy of the question text, the learner's answer and the correct answer, so
    UC-06 can render a feedback report without reading UC-02 or UC-03 again -- which is what keeps a
    historical report identical after the question bank changes.
    """

    __tablename__ = f"{TABLE_PREFIX}question_scores"

    id: Mapped[str] = id_column()
    result_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}attempt_results.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Soft references to UC-03's frozen question row and UC-02's question.
    attempt_question_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_version: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    awarded_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    maximum_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Marks before the per-question floor at zero. Kept so a deduction is explainable.
    raw_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    deduction: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    answered: Mapped[bool] = mapped_column(Boolean(create_constraint=False), nullable=False)

    question_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scenario_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Topic names frozen at snapshot time; UC-06 resolves a lesson reference from them.
    topics: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)

    learner_answer: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    learner_answer_display: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    correct_answer_display: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    #: Per option: selected, correct, and the marks it contributed. Required for MULTI_SELECT.
    option_marks: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )

    anomaly: Mapped[str | None] = mapped_column(String(64), nullable=True)
    answer_key_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False)

    result: Mapped[AttemptResult] = relationship(back_populates="question_scores")

    __table_args__ = (
        CheckConstraint(f"outcome IN ({_OUTCOME_VALUES})", name="outcome"),
        CheckConstraint(
            "question_type IN ('SINGLE_CHOICE', 'TRUE_FALSE', 'MULTI_SELECT', "
            "'SCENARIO', 'DRAG_TO_ORDER')",
            name="question_type",
        ),
        CheckConstraint("position >= 1", name="position"),
        CheckConstraint("answered IN (0, 1)", name="answered"),
        CheckConstraint("awarded_marks >= 0 AND maximum_marks >= 0", name="marks_non_negative"),
        CheckConstraint("awarded_marks <= maximum_marks", name="marks_within_maximum"),
        CheckConstraint("deduction >= 0", name="deduction_non_negative"),
        # An unanswered or unscored question can never carry marks.
        CheckConstraint(
            "outcome NOT IN ('UNANSWERED', 'NOT_SCORED') OR awarded_marks = 0",
            name="zero_marks_when_not_scored",
        ),
        UniqueConstraint("result_id", "position", name="result_id_position"),
        UniqueConstraint("result_id", "question_id", name="result_id_question_id"),
        Index(f"ix_{TABLE_PREFIX}question_scores_question_id", "question_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<QuestionScoreRow {self.question_id} {self.awarded_marks}/{self.maximum_marks}>"


# ---------------------------------------------------------------------------
# Integrity triggers
#
# The one UC-04 guarantee that application code must not be the only thing protecting: a confirmed
# score can never be edited. Same mechanism UC-01 uses for its immutable configuration versions,
# emitted per dialect so the guarantee survives the move to the company database.
#
# UPDATE only. DELETE is left to referential integrity and to the service, exactly as UC-01 does:
# nothing in the application ever deletes a result, and a blanket DELETE trigger would also block
# a test database being truncated between tests.
# ---------------------------------------------------------------------------

IMMUTABLE_RESULT_MESSAGE = "IMMUTABLE_ATTEMPT_RESULT: a confirmed score cannot be modified"
IMMUTABLE_QUESTION_SCORE_MESSAGE = (
    "IMMUTABLE_QUESTION_SCORE: per-question scores are written once and cannot be modified"
)

_RESULTS = f"{TABLE_PREFIX}attempt_results"
_QUESTION_SCORES = f"{TABLE_PREFIX}question_scores"

RESULT_TRIGGER = "trg_qr_result_immutable_when_scored"
QUESTION_SCORE_TRIGGER = "trg_qr_question_score_no_update"

#: Table -> trigger, consumed by both the ``create_all`` hooks below and the Alembic migration, so a
#: migrated database and a ``create_all`` database cannot end up with different guarantees.
IMMUTABLE_TABLES: tuple[tuple[str, str], ...] = (
    (_RESULTS, RESULT_TRIGGER),
    (_QUESTION_SCORES, QUESTION_SCORE_TRIGGER),
)

POSTGRES_RESULT_FN = f"""
CREATE OR REPLACE FUNCTION fn_reject_scored_result_update()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION '{IMMUTABLE_RESULT_MESSAGE}';
END; $$ LANGUAGE plpgsql;
"""

POSTGRES_QUESTION_SCORE_FN = f"""
CREATE OR REPLACE FUNCTION fn_reject_question_score_update()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION '{IMMUTABLE_QUESTION_SCORE_MESSAGE}';
END; $$ LANGUAGE plpgsql;
"""


def _sqlite_statements() -> dict[str, list[str]]:
    return {
        _RESULTS: [
            f"""
CREATE TRIGGER {RESULT_TRIGGER}
BEFORE UPDATE ON {_RESULTS}
WHEN OLD.status = '{ResultStatus.SCORED.value}'
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_RESULT_MESSAGE}');
END;
"""
        ],
        _QUESTION_SCORES: [
            f"""
CREATE TRIGGER {QUESTION_SCORE_TRIGGER}
BEFORE UPDATE ON {_QUESTION_SCORES}
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_QUESTION_SCORE_MESSAGE}');
END;
"""
        ],
    }


def _postgres_statements() -> dict[str, list[str]]:
    return {
        _RESULTS: [
            POSTGRES_RESULT_FN,
            f"""
CREATE TRIGGER {RESULT_TRIGGER}
BEFORE UPDATE ON {_RESULTS}
FOR EACH ROW WHEN (OLD.status = '{ResultStatus.SCORED.value}')
EXECUTE FUNCTION fn_reject_scored_result_update();
""",
        ],
        _QUESTION_SCORES: [
            POSTGRES_QUESTION_SCORE_FN,
            f"""
CREATE TRIGGER {QUESTION_SCORE_TRIGGER}
BEFORE UPDATE ON {_QUESTION_SCORES}
FOR EACH ROW EXECUTE FUNCTION fn_reject_question_score_update();
""",
        ],
    }


def sqlite_trigger_statements() -> list[str]:
    """SQLite DDL for UC-04's immutability triggers. Used by the Alembic migration."""
    return [statement for group in _sqlite_statements().values() for statement in group]


def postgres_trigger_statements() -> list[str]:
    """PostgreSQL DDL for the same guarantees, functions first."""
    return [statement for group in _postgres_statements().values() for statement in group]


def trigger_names() -> list[str]:
    return [trigger for _table, trigger in IMMUTABLE_TABLES]


def trigger_table(trigger_name: str) -> str:
    for table, trigger in IMMUTABLE_TABLES:
        if trigger == trigger_name:
            return table
    raise ValueError(f"unknown trigger {trigger_name}")


def _attach_triggers() -> None:
    """Emit each trigger right after the table it protects is created."""
    sqlite_by_table = _sqlite_statements()
    postgres_by_table = _postgres_statements()

    for table_name, statements in sqlite_by_table.items():
        table = Base.metadata.tables[table_name]
        for statement in statements:
            event.listen(table, "after_create", DDL(statement).execute_if(dialect="sqlite"))
        for statement in postgres_by_table[table_name]:
            event.listen(table, "after_create", DDL(statement).execute_if(dialect="postgresql"))


_attach_triggers()


def is_immutability_violation(error: BaseException) -> bool:
    """True when a database trigger rejected an edit to a confirmed score."""
    text = str(error)
    return IMMUTABLE_RESULT_MESSAGE in text or IMMUTABLE_QUESTION_SCORE_MESSAGE in text
