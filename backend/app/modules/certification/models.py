"""SQLAlchemy ORM models for UC-05 Pass / Fail & Certificate Gating.

Tables are prefixed ``qg_`` (quiz *gating*).

The guarantees that must not depend on application code alone:

* ``uq_qg_attempt_outcomes_attempt_id`` -- one outcome per attempt. Determining pass/fail twice is
  impossible, so a retry cannot flip a learner from fail to pass because the quiz was reconfigured.
* ``trg_qg_outcome_no_update`` -- an outcome row can never be updated. It is a derived fact about an
  immutable score, so it has no legitimate reason to change.
* ``uq_qg_certificates_attempt_id`` -- one certificate record per attempt, which is what makes
  "request a certificate" idempotent.
* ``ux_qg_certificate_single_issued`` -- a partial unique index permitting **at most one ISSUED
  certificate per learner and quiz**. This is the real duplicate-prevention guarantee: passing a
  second time does not mint a second certificate, and two concurrent issue calls cannot both win.
* ``uq_qg_cpd_records_attempt_id`` -- one CPD record per attempt, so a retried synchronisation
  updates the existing row instead of double-reporting the learner's CPD.

Cross-boundary references (``attempt_id``, ``result_id``, ``learner_id``, ``quiz_id``,
``course_id``, ``configuration_version_id``) are soft: indexed columns without foreign keys, for the
same reason UC-03 and UC-04 do it -- the rows belong to other capabilities and this schema must
survive them being superseded.
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
    Index,
    Integer,
    String,
    Text,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, id_column
from app.modules.certification.domain.enums import CertificateStatus, CpdSyncStatus, Outcome

TABLE_PREFIX = "qg_"

_OUTCOME_VALUES = ", ".join(f"'{item.value}'" for item in Outcome)
_CERTIFICATE_STATUS_VALUES = ", ".join(f"'{item.value}'" for item in CertificateStatus)
_CPD_STATUS_VALUES = ", ".join(f"'{item.value}'" for item in CpdSyncStatus)


class AttemptOutcome(Base):
    """The pass/fail determination for one attempt.

    Stores the pass mark it was judged against, so the decision is explainable years later without
    reading UC-01 at all -- and remains explainable after that configuration version is superseded.
    """

    __tablename__ = f"{TABLE_PREFIX}attempt_outcomes"

    id: Mapped[str] = id_column()

    #: Soft references to UC-03's attempt and UC-04's result.
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    result_id: Mapped[str] = mapped_column(String(36), nullable=False)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_version_id: Mapped[str] = mapped_column(String(64), nullable=False)

    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    percentage: Mapped[float] = mapped_column(Float, nullable=False)
    #: The pass mark of the attempt's own configuration version -- not the quiz's current one.
    pass_mark_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    total_marks: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_marks: Mapped[float] = mapped_column(Float, nullable=False)

    #: The attempts picture at the moment of determination. The live figure is recomputed on read;
    #: this is the audit copy, which is why it carries "at outcome" in its name.
    attempts_used_at_outcome: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts_remaining_at_outcome: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Whether a certificate is due for this outcome. True only for a pass.
    certificate_required: Mapped[bool] = mapped_column(
        Boolean(create_constraint=False), nullable=False, default=False
    )

    determined_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(f"outcome IN ({_OUTCOME_VALUES})", name="outcome"),
        CheckConstraint("percentage >= 0 AND percentage <= 100", name="percentage_range"),
        CheckConstraint(
            "pass_mark_percentage >= 0 AND pass_mark_percentage <= 100", name="pass_mark_range"
        ),
        CheckConstraint("total_marks >= 0 AND maximum_marks >= 0", name="marks_non_negative"),
        CheckConstraint("attempts_used_at_outcome >= 0", name="attempts_used_non_negative"),
        CheckConstraint("max_attempts IS NULL OR max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint(
            "attempts_remaining_at_outcome IS NULL OR attempts_remaining_at_outcome >= 0",
            name="attempts_remaining_non_negative",
        ),
        CheckConstraint("certificate_required IN (TRUE, FALSE)", name="certificate_required"),
        # A certificate is due exactly when the learner passed.
        CheckConstraint(
            "(outcome = 'PASS') = certificate_required", name="certificate_follows_outcome"
        ),
        Index(f"ix_{TABLE_PREFIX}attempt_outcomes_learner_quiz", "learner_id", "quiz_id"),
        Index(f"ix_{TABLE_PREFIX}attempt_outcomes_outcome", "outcome"),
        Index(f"ix_{TABLE_PREFIX}attempt_outcomes_result_id", "result_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AttemptOutcome attempt={self.attempt_id} {self.outcome} {self.percentage}%>"


class Certificate(Base):
    """One certificate request for one passing attempt.

    The row exists as soon as the pass is determined, which is what makes issue retryable: the
    request is durable even when the certificate service is unreachable.
    """

    __tablename__ = f"{TABLE_PREFIX}certificates"

    id: Mapped[str] = id_column()

    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    outcome_id: Mapped[str] = mapped_column(String(36), nullable=False)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Frozen at request time so the certificate keeps meaning what it meant if the course is
    #: renamed.
    course_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quiz_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    percentage: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CertificateStatus.PENDING.value
    )
    #: The certificate service's identifier for the issued document.
    certificate_number: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    document_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Anything else the certificate service returned. Kept verbatim rather than parsed.
    metadata_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )

    #: Generation attempts, so a permanently failing certificate is visible rather than silent.
    generation_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(nullable=False)
    last_attempted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({_CERTIFICATE_STATUS_VALUES})", name="status"),
        CheckConstraint("percentage >= 0 AND percentage <= 100", name="percentage_range"),
        CheckConstraint(
            "generation_attempt_count >= 0", name="generation_attempt_count_non_negative"
        ),
        # An issued certificate always has a number and an instant; a pending one has neither.
        CheckConstraint("(status = 'ISSUED') = (issued_at IS NOT NULL)", name="issued_state"),
        CheckConstraint(
            "status <> 'ISSUED' OR certificate_number IS NOT NULL", name="issued_has_number"
        ),
        # THE duplicate-prevention guarantee: one issued certificate per learner and quiz, whatever
        # the application layer does, and whichever of two concurrent calls gets there first.
        Index(
            "ux_qg_certificate_single_issued",
            "learner_id",
            "quiz_id",
            unique=True,
            sqlite_where=text("status = 'ISSUED'"),
            postgresql_where=text("status = 'ISSUED'"),
        ),
        Index(f"ix_{TABLE_PREFIX}certificates_status", "status", "last_attempted_at"),
        Index(f"ix_{TABLE_PREFIX}certificates_learner_id", "learner_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Certificate attempt={self.attempt_id} {self.status}>"


class CpdRecord(Base):
    """One CPD synchronisation for one attempt.

    The four fields the CPD system needs -- attempt date, score, pass/fail and course name -- are
    frozen here at determination time, so a retry days later sends exactly what the attempt earned
    rather than whatever the course happens to be called now.
    """

    __tablename__ = f"{TABLE_PREFIX}cpd_records"

    id: Mapped[str] = id_column()

    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    outcome_id: Mapped[str] = mapped_column(String(36), nullable=False)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The four CPD fields.
    attempt_date: Mapped[datetime] = mapped_column(nullable=False)
    score_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean(create_constraint=False), nullable=False)
    course_name: Mapped[str] = mapped_column(String(255), nullable=False)

    total_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    maximum_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CpdSyncStatus.PENDING.value
    )
    external_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sync_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(nullable=False)
    last_attempted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    synchronised_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({_CPD_STATUS_VALUES})", name="status"),
        CheckConstraint("passed IN (TRUE, FALSE)", name="passed"),
        CheckConstraint(
            "score_percentage >= 0 AND score_percentage <= 100", name="score_percentage_range"
        ),
        CheckConstraint("sync_attempt_count >= 0", name="sync_attempt_count_non_negative"),
        CheckConstraint(
            "(status = 'SYNCHRONISED') = (synchronised_at IS NOT NULL)", name="synchronised_state"
        ),
        Index(f"ix_{TABLE_PREFIX}cpd_records_status", "status", "last_attempted_at"),
        Index(f"ix_{TABLE_PREFIX}cpd_records_learner_id", "learner_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CpdRecord attempt={self.attempt_id} {self.status}>"


# ---------------------------------------------------------------------------
# Integrity trigger
#
# A pass/fail outcome is a derived fact about an immutable score, so it is written once and never
# edited. Certificates and CPD records are deliberately NOT frozen this way: their whole purpose is
# to move from PENDING to ISSUED / SYNCHRONISED, and a retry has to be able to record its attempt.
# UPDATE only, for the same reasons UC-01 and UC-04 give.
# ---------------------------------------------------------------------------

IMMUTABLE_OUTCOME_MESSAGE = (
    "IMMUTABLE_ATTEMPT_OUTCOME: a determined pass/fail outcome cannot be modified"
)

_OUTCOMES = f"{TABLE_PREFIX}attempt_outcomes"

OUTCOME_TRIGGER = "trg_qg_outcome_no_update"

IMMUTABLE_TABLES: tuple[tuple[str, str], ...] = ((_OUTCOMES, OUTCOME_TRIGGER),)

POSTGRES_OUTCOME_FN = f"""
CREATE OR REPLACE FUNCTION fn_reject_attempt_outcome_update()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION '{IMMUTABLE_OUTCOME_MESSAGE}';
END; $$ LANGUAGE plpgsql;
"""

SQLITE_OUTCOME_TRIGGER = f"""
CREATE TRIGGER {OUTCOME_TRIGGER}
BEFORE UPDATE ON {_OUTCOMES}
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_OUTCOME_MESSAGE}');
END;
"""

POSTGRES_OUTCOME_TRIGGER = f"""
CREATE TRIGGER {OUTCOME_TRIGGER}
BEFORE UPDATE ON {_OUTCOMES}
FOR EACH ROW EXECUTE FUNCTION fn_reject_attempt_outcome_update();
"""


def sqlite_trigger_statements() -> list[str]:
    """SQLite DDL for UC-05's immutability trigger. Used by the Alembic migration."""
    return [SQLITE_OUTCOME_TRIGGER]


def postgres_trigger_statements() -> list[str]:
    """PostgreSQL DDL for the same guarantee, function first."""
    return [POSTGRES_OUTCOME_FN, POSTGRES_OUTCOME_TRIGGER]


def trigger_names() -> list[str]:
    return [trigger for _table, trigger in IMMUTABLE_TABLES]


def trigger_table(trigger_name: str) -> str:
    for table, trigger in IMMUTABLE_TABLES:
        if trigger == trigger_name:
            return table
    raise ValueError(f"unknown trigger {trigger_name}")


def _attach_triggers() -> None:
    table = Base.metadata.tables[_OUTCOMES]
    event.listen(table, "after_create", DDL(SQLITE_OUTCOME_TRIGGER).execute_if(dialect="sqlite"))
    for statement in (POSTGRES_OUTCOME_FN, POSTGRES_OUTCOME_TRIGGER):
        event.listen(table, "after_create", DDL(statement).execute_if(dialect="postgresql"))


_attach_triggers()


def is_immutability_violation(error: BaseException) -> bool:
    """True when the database trigger rejected an edit to a determined outcome."""
    return IMMUTABLE_OUTCOME_MESSAGE in str(error)
