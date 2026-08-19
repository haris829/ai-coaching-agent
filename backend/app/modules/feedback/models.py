"""SQLAlchemy ORM models for UC-06 Detailed Feedback Report.

Tables are prefixed ``qf_`` (quiz *feedback*).

The guarantees:

* ``uq_qf_feedback_reports_attempt_id`` -- one report per attempt, which is what makes generation
  idempotent and a retry safe.
* ``trg_qf_report_immutable_when_generated`` -- a trigger rejecting any ``UPDATE`` to a report once
  it is ``GENERATED``, and ``trg_qf_item_no_update`` doing the same for every item. Together with
  the stored ``payload`` -- the complete report as it was rendered -- this is what makes historical
  feedback consistent even after the questions, topics or configuration change. The requirement is
  met by the schema rather than by everybody remembering not to rewrite a report.
* per-question rows carry their own frozen copy of the question, the answers, the explanation and
  the lesson reference, so the report is readable field by field in SQL and not only as one JSON
  blob.

Cross-boundary references (``attempt_id``, ``result_id``, ``outcome_id``, ``question_id``) are soft,
as in every other capability here.
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
from app.modules.feedback.domain.enums import ReportStatus

TABLE_PREFIX = "qf_"

_STATUS_VALUES = ", ".join(f"'{item.value}'" for item in ReportStatus)


class FeedbackReportRow(Base):
    """One attempt's feedback report."""

    __tablename__ = f"{TABLE_PREFIX}feedback_reports"

    id: Mapped[str] = id_column()

    #: Soft references to UC-03's attempt, UC-04's result and UC-05's outcome.
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    outcome_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ReportStatus.PENDING.value
    )

    total_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    maximum_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pass_mark_percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: NULL when pass/fail had not been determined when the report was generated.
    passed: Mapped[bool | None] = mapped_column(Boolean(create_constraint=False), nullable=True)

    time_taken_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incorrect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unanswered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: The complete rendered report. Served verbatim once generated, which is what guarantees a
    #: historical report reads identically however the question bank changes afterwards.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)

    generation_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    items: Mapped[list[FeedbackItemRow]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="FeedbackItemRow.position",
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status"),
        CheckConstraint("total_marks >= 0 AND maximum_marks >= 0", name="marks_non_negative"),
        CheckConstraint("percentage >= 0 AND percentage <= 100", name="percentage_range"),
        CheckConstraint(
            "time_taken_seconds IS NULL OR time_taken_seconds >= 0", name="time_taken_non_negative"
        ),
        CheckConstraint("passed IS NULL OR passed IN (0, 1)", name="passed"),
        CheckConstraint(
            "generation_attempt_count >= 0", name="generation_attempt_count_non_negative"
        ),
        # A generated report always says when, and always carries its payload.
        CheckConstraint(
            "(status = 'GENERATED') = (generated_at IS NOT NULL)", name="generated_state"
        ),
        CheckConstraint(
            "status <> 'GENERATED' OR payload IS NOT NULL", name="generated_has_payload"
        ),
        Index(f"ix_{TABLE_PREFIX}feedback_reports_learner_quiz", "learner_id", "quiz_id"),
        Index(f"ix_{TABLE_PREFIX}feedback_reports_status", "status"),
        Index(f"ix_{TABLE_PREFIX}feedback_reports_result_id", "result_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FeedbackReport attempt={self.attempt_id} {self.status}>"


class FeedbackItemRow(Base):
    """One question's feedback within a report. Written once, never edited."""

    __tablename__ = f"{TABLE_PREFIX}feedback_items"

    id: Mapped[str] = id_column()
    report_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}feedback_reports.id", ondelete="CASCADE"),
        nullable=False,
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_version: Mapped[int] = mapped_column(Integer, nullable=False)
    question_reference: Mapped[str | None] = mapped_column(String(32), nullable=True)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)

    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Never NULL: a question with no authored explanation carries the defined fallback text.
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    #: Never NULL either, for the same reason.
    lesson_reference: Mapped[str] = mapped_column(String(255), nullable=False)

    learner_answer: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    correct_answer: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    #: Per option: correct/incorrect, whether it was selected, and its mark contribution.
    option_breakdown: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )

    question_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    maximum_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    deduction: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    answered: Mapped[bool] = mapped_column(Boolean(create_constraint=False), nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False)

    report: Mapped[FeedbackReportRow] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint("position >= 1", name="position"),
        CheckConstraint("answered IN (0, 1)", name="answered"),
        CheckConstraint(
            "question_score >= 0 AND maximum_marks >= 0 AND deduction >= 0",
            name="marks_non_negative",
        ),
        CheckConstraint("question_score <= maximum_marks", name="marks_within_maximum"),
        UniqueConstraint("report_id", "position", name="report_id_position"),
        UniqueConstraint("report_id", "question_id", name="report_id_question_id"),
        Index(f"ix_{TABLE_PREFIX}feedback_items_question_id", "question_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FeedbackItem {self.question_id} pos={self.position}>"


# ---------------------------------------------------------------------------
# Integrity triggers
#
# "Historical feedback must remain consistent even if questions or configuration later change" is a
# claim about data, so it is enforced by the database: a generated report cannot be updated, and an
# item cannot be updated at all. UPDATE only, as everywhere else here -- see the note in UC-04's
# models for why a DELETE trigger would be the wrong tool.
# ---------------------------------------------------------------------------

IMMUTABLE_REPORT_MESSAGE = (
    "IMMUTABLE_FEEDBACK_REPORT: a generated feedback report cannot be modified"
)
IMMUTABLE_ITEM_MESSAGE = (
    "IMMUTABLE_FEEDBACK_ITEM: feedback items are written once and cannot be modified"
)

_REPORTS = f"{TABLE_PREFIX}feedback_reports"
_ITEMS = f"{TABLE_PREFIX}feedback_items"

REPORT_TRIGGER = "trg_qf_report_immutable_when_generated"
ITEM_TRIGGER = "trg_qf_item_no_update"

IMMUTABLE_TABLES: tuple[tuple[str, str], ...] = (
    (_REPORTS, REPORT_TRIGGER),
    (_ITEMS, ITEM_TRIGGER),
)

POSTGRES_REPORT_FN = f"""
CREATE OR REPLACE FUNCTION fn_reject_generated_report_update()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION '{IMMUTABLE_REPORT_MESSAGE}';
END; $$ LANGUAGE plpgsql;
"""

POSTGRES_ITEM_FN = f"""
CREATE OR REPLACE FUNCTION fn_reject_feedback_item_update()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION '{IMMUTABLE_ITEM_MESSAGE}';
END; $$ LANGUAGE plpgsql;
"""


def _sqlite_statements() -> dict[str, list[str]]:
    return {
        _REPORTS: [
            f"""
CREATE TRIGGER {REPORT_TRIGGER}
BEFORE UPDATE ON {_REPORTS}
WHEN OLD.status = '{ReportStatus.GENERATED.value}'
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_REPORT_MESSAGE}');
END;
"""
        ],
        _ITEMS: [
            f"""
CREATE TRIGGER {ITEM_TRIGGER}
BEFORE UPDATE ON {_ITEMS}
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_ITEM_MESSAGE}');
END;
"""
        ],
    }


def _postgres_statements() -> dict[str, list[str]]:
    return {
        _REPORTS: [
            POSTGRES_REPORT_FN,
            f"""
CREATE TRIGGER {REPORT_TRIGGER}
BEFORE UPDATE ON {_REPORTS}
FOR EACH ROW WHEN (OLD.status = '{ReportStatus.GENERATED.value}')
EXECUTE FUNCTION fn_reject_generated_report_update();
""",
        ],
        _ITEMS: [
            POSTGRES_ITEM_FN,
            f"""
CREATE TRIGGER {ITEM_TRIGGER}
BEFORE UPDATE ON {_ITEMS}
FOR EACH ROW EXECUTE FUNCTION fn_reject_feedback_item_update();
""",
        ],
    }


def sqlite_trigger_statements() -> list[str]:
    """SQLite DDL for UC-06's immutability triggers. Used by the Alembic migration."""
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
    """True when a database trigger rejected an edit to generated feedback."""
    text = str(error)
    return IMMUTABLE_REPORT_MESSAGE in text or IMMUTABLE_ITEM_MESSAGE in text
