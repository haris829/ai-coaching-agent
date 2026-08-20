"""SQLAlchemy ORM models for UC-10 Analytics & Reporting.

Tables are prefixed ``qy_`` so this schema sits alongside every other capability's.

**Two tables, and they are the module's only write surface.** UC-10 reads attempts, answers,
scores and outcomes — and cannot write them: there is no model here for any of it, and the
read-only projection that fetches them (``integration/assessment_repository.py``) issues nothing
but ``select`` statements. What UC-10 owns is the *consequence* of analysis:

``qy_question_flags``
    Which questions the thresholds have flagged for content review, and their current state.

``qy_review_actions``
    An append-only record of every review decision — who, what, when. Never updated, never
    deleted.

**A flag clears only through a review action.** That is the requirement, and it is why the two
tables exist together: ``qy_question_flags`` holds the current state, and every transition of it is
accompanied by a row in ``qy_review_actions`` naming the administrator who caused it. The audit
table's rows are immutable by trigger as well as by service, so "who cleared this flag?" cannot
become unanswerable.

Cross-boundary references (``question_id``, ``admin_id``) are **soft**: indexed columns without
foreign keys, following the precedent every other capability set. A question can be deleted from
the catalogue while the record of its review survives — and it must, or retiring a question would
erase the evidence for retiring it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DDL,
    CheckConstraint,
    Float,
    Index,
    Integer,
    String,
    Text,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.analytics.domain.enums import FlagReason, FlagStatus, ReviewActionType

TABLE_PREFIX = "qy_"


def _values(enum_class: type) -> str:
    """``'A', 'B'`` — a CHECK constraint built from the domain's own vocabulary."""
    return ", ".join(f"'{member.value}'" for member in enum_class)


class QuestionFlagRow(Base):
    """The current content-review state of one question."""

    __tablename__ = f"{TABLE_PREFIX}question_flags"

    #: The question *is* the identity: a question has at most one current flag state. That is what
    #: makes ``upsert_flag`` idempotent without an idempotency key.
    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)

    #: The evidence at the moment of flagging, kept so a later reader can see *why* — a flag whose
    #: threshold has since been changed is still explicable.
    wrong_answer_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    graded_responses_at_flag: Mapped[int | None] = mapped_column(Integer, nullable=True)

    flagged_at: Mapped[datetime] = mapped_column(nullable=False)
    #: The administrator or process that raised it. ``system`` for threshold evaluation.
    flagged_by: Mapped[str] = mapped_column(String(128), nullable=False)

    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Which review action resolved it. NULL while the flag stands.
    resolution_action: Mapped[str | None] = mapped_column(String(32), nullable=True)

    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_values(FlagStatus)})", name="ck_flag_status"),
        CheckConstraint(f"reason IN ({_values(FlagReason)})", name="ck_flag_reason"),
        CheckConstraint(
            f"resolution_action IS NULL OR resolution_action IN ({_values(ReviewActionType)})",
            name="ck_flag_resolution_action",
        ),
        CheckConstraint(
            "wrong_answer_rate IS NULL OR (wrong_answer_rate >= 0 AND wrong_answer_rate <= 100)",
            name="ck_flag_rate_range",
        ),
        CheckConstraint(
            "graded_responses_at_flag IS NULL OR graded_responses_at_flag >= 0",
            name="ck_flag_responses",
        ),
        # A resolved flag always names who resolved it and when. An unattributed resolution is
        # exactly what the audit requirement exists to prevent, so it is not representable.
        CheckConstraint(
            "(resolved_at IS NULL AND resolved_by IS NULL) "
            "OR (resolved_at IS NOT NULL AND resolved_by IS NOT NULL)",
            name="ck_flag_resolution_attribution",
        ),
        # The review queue an administrator opens, and the re-flag sweep.
        Index("ix_flag_status", "status", "flagged_at"),
    )


class ReviewActionRow(Base):
    """One administrator's review decision. Append-only.

    ``No Change`` is recorded as deliberately as ``Question Retired``: an administrator who looked
    at a flagged question and decided nothing needed changing has made a decision, and a queue that
    forgot it would keep presenting the same question forever.
    """

    __tablename__ = f"{TABLE_PREFIX}review_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)

    action: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The administrator, resolved from the identity seam — never a body field.
    admin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The flag state either side of this action, so the audit trail explains a transition rather
    #: than merely recording that one happened.
    previous_flag_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    resulting_flag_status: Mapped[str | None] = mapped_column(String(24), nullable=True)

    __table_args__ = (
        CheckConstraint(f"action IN ({_values(ReviewActionType)})", name="ck_action_type"),
        CheckConstraint(
            f"previous_flag_status IS NULL OR previous_flag_status IN ({_values(FlagStatus)})",
            name="ck_action_previous_status",
        ),
        CheckConstraint(
            f"resulting_flag_status IS NULL OR resulting_flag_status IN ({_values(FlagStatus)})",
            name="ck_action_resulting_status",
        ),
        # The audit log an administrator reads, newest first, filterable by question and by admin.
        Index("ix_action_question", "question_id", "created_at"),
        Index("ix_action_admin", "admin_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Immutability, enforced by the database
# ---------------------------------------------------------------------------
#
# The audit table's rows are immutable, and a service promising not to rewrite one is not the same
# as a database refusing to. Following the pattern UC-05, UC-06 and UC-07 established for their own
# append-only tables: a trigger per dialect, attached to ``after_create`` so a ``create_all``
# database and a migrated one carry the same guarantee, with the DDL defined once here and imported
# by the migration rather than copied into it.
#
# **UPDATE is refused; DELETE is not.** That is the same line every other capability draws, and it
# is drawn deliberately. Rewriting an audit row would make "who cleared this flag?" answerable but
# wrong, which is worse than unanswerable — so the database refuses it outright. Deletion is a
# different question: data-retention and erasure obligations are real, and a trigger that blocked
# them would have to be dropped to satisfy one, at which point the guarantee is gone anyway. What
# stops UC-10 deleting an audit row is that no method exists to do it — the protocol declares none
# and the repository implements none — which is the same way UC-08 protects its grants.

# The ``IMMUTABLE_`` prefix is the convention every other immutability trigger in this system
# follows (``IMMUTABLE_CONFIGURATION_VERSION``, ``IMMUTABLE_ATTEMPT_RESULT``, and six more). It is
# what lets a caller reading a raw database error tell an immutability refusal from a missing column
# or a broken connection, without matching on prose. UC-10 was integrated last and had drifted from
# it; the wording is aligned here so all eleven triggers are recognisable the same way.
_SQLITE_ACTION_TRIGGER = f"""
CREATE TRIGGER IF NOT EXISTS trg_{TABLE_PREFIX}review_action_no_update
BEFORE UPDATE ON {TABLE_PREFIX}review_actions
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE_REVIEW_ACTION: review actions are append-only');
END
"""

_POSTGRES_ACTION_FUNCTION = f"""
CREATE OR REPLACE FUNCTION fn_reject_{TABLE_PREFIX}review_action_change()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_REVIEW_ACTION: review actions are append-only';
END;
$$ LANGUAGE plpgsql
"""

_POSTGRES_ACTION_TRIGGER = f"""
CREATE TRIGGER trg_{TABLE_PREFIX}review_action_no_update
BEFORE UPDATE ON {TABLE_PREFIX}review_actions
FOR EACH ROW EXECUTE FUNCTION fn_reject_{TABLE_PREFIX}review_action_change()
"""


def trigger_names() -> list[str]:
    """Every trigger this module defines, for the migration and the schema test."""
    return [f"trg_{TABLE_PREFIX}review_action_no_update"]


def trigger_table(trigger_name: str) -> str:
    if trigger_name in trigger_names():
        return f"{TABLE_PREFIX}review_actions"
    raise ValueError(f"unknown trigger {trigger_name}")


def sqlite_trigger_statements() -> list[str]:
    return [_SQLITE_ACTION_TRIGGER]


def postgres_trigger_statements() -> list[str]:
    return [_POSTGRES_ACTION_FUNCTION, _POSTGRES_ACTION_TRIGGER]


def is_immutability_violation(exc: BaseException) -> bool:
    """Whether an exception is one of this module's triggers firing."""
    return "append-only" in str(exc)


def _attach_triggers() -> None:
    """Emit each trigger right after the table it protects is created."""
    table = ReviewActionRow.__table__
    for statement in sqlite_trigger_statements():
        event.listen(table, "after_create", DDL(statement).execute_if(dialect="sqlite"))
    for statement in postgres_trigger_statements():
        event.listen(table, "after_create", DDL(statement).execute_if(dialect="postgresql"))


_attach_triggers()

#: Referenced so a reader can see the guard is armed at import time rather than by a side effect.
IMMUTABLE_TABLES: tuple[tuple[str, str], ...] = tuple(
    (trigger_table(name), name) for name in trigger_names()
)

__all__ = [
    "IMMUTABLE_TABLES",
    "QuestionFlagRow",
    "ReviewActionRow",
    "TABLE_PREFIX",
    "is_immutability_violation",
    "postgres_trigger_statements",
    "sqlite_trigger_statements",
    "trigger_names",
    "trigger_table",
]

# Silence the unused-import warning for `text`, which the DDL strings above do not need but which
# a future partial index will.
_ = text
