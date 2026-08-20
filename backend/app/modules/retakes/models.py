"""SQLAlchemy ORM models for UC-08 Retake Management.

Tables are prefixed ``qt_`` so this schema sits alongside UC-01's ``qc_``, UC-02's ``qb_``,
UC-03's ``qd_``, UC-04's ``qr_``, UC-05's ``qg_``, UC-06's ``qf_`` and UC-07's ``qk_``.

UC-08 shipped with no schema at all — persistence was two Protocols, and
``backend/docs/INTEGRATION.md`` §4 said which two tables would satisfy them. This file is that
section carried out. Nothing in the domain or the services changed to make it possible; the
in-memory binding they were written and tested against is still here, in ``repositories/``,
and still implements the same contract.

**The two constraints below carry the module's correctness argument, and both are enforced by
the database rather than by application code.** A read followed by a write has a window between
the two, and that window is precisely the race a retake reservation exists to close:

``ux_retake_idempotency``
    One retake per ``retake:<learner>:<quiz>:<previous attempt>``. A client that retries after a
    timeout produces the same key, loses the insert, and is handed the attempt that already
    exists — a replay becomes a read.

``ux_retake_attempt_slot``
    One holder of ``(learner_id, quiz_id, attempt_number)`` among non-``FAILED`` rows. Two
    simultaneous retakes compute the same next attempt number; exactly one insert survives. The
    partial ``WHERE`` clause is what lets a failed retake be retried into its own slot without
    deleting anything — and nothing here is ever deleted.

Cross-boundary references (``learner_id``, ``course_id``, ``quiz_id``, ``previous_attempt_id``,
``attempt_id``, ``configuration_version_id``) are **soft**: indexed columns without foreign keys,
following UC-03's precedent, because those rows are owned by other capabilities and UC-08 must
survive them being superseded. UC-08 holds no score, no answer and no question content, so there
is nothing here that could contradict them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TABLE_PREFIX = "qt_"


class RetakeRequestRow(Base):
    """One retake, from reservation through to the attempt it produced."""

    __tablename__ = "qt_retake_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    #: ``retake:<learner>:<quiz>:<previous attempt>`` — derived, never client-supplied.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The submitted attempt this retake follows. Read-only to UC-08 in every sense: there is no
    #: code path in the module that writes to an attempt row.
    previous_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)

    #: The reserved slot. Unique per learner+quiz among non-FAILED rows.
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    configuration_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    configuration_version_source: Mapped[str] = mapped_column(String(24), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Set once UC-03 has created the attempt; NULL while RESERVED or after a failure.
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Snapshots of what the retake was planned and delivered under, so "why did I see that
    #: question again?" stays answerable later without re-deriving the bank as it was.
    question_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    question_set_difference: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    anomalies: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Incremented on each retry of a failed request, so "worked first time" and "worked on the
    #: third go" are distinguishable in the audit trail.
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('RESERVED', 'COMPLETED', 'FAILED')", name="ck_retake_status"
        ),
        CheckConstraint(
            "configuration_version_source IN "
            "('CARRIED_FORWARD', 'ADVANCED_TO_ACTIVE', 'PINNED_TO_PREVIOUS')",
            name="ck_retake_version_source",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_retake_attempt_number"),
        CheckConstraint("attempt_count >= 1", name="ck_retake_attempt_count"),
        # A COMPLETED retake always names the attempt it produced; a RESERVED one never can,
        # because the attempt does not exist yet. Makes an inconsistent record unrepresentable.
        CheckConstraint(
            "(status = 'COMPLETED' AND attempt_id IS NOT NULL AND completed_at IS NOT NULL) "
            "OR (status = 'RESERVED' AND attempt_id IS NULL) "
            "OR (status = 'FAILED')",
            name="ck_retake_completion_state",
        ),
        Index("ux_retake_idempotency", "idempotency_key", unique=True),
        # The reservation. See the module docstring — this index *is* the concurrency guarantee.
        Index(
            "ux_retake_attempt_slot",
            "learner_id",
            "quiz_id",
            "attempt_number",
            unique=True,
            sqlite_where=text("status <> 'FAILED'"),
            postgresql_where=text("status <> 'FAILED'"),
        ),
        Index("ix_retake_learner_quiz", "learner_id", "quiz_id", "status"),
        Index("ix_retake_previous_attempt", "previous_attempt_id"),
        Index("ix_retake_attempt", "attempt_id"),
    )


class AdditionalAttemptGrantRow(Base):
    """One administrator grant of extra attempts to one learner on one course and quiz.

    Never deleted. Revocation is a status transition, because "who gave this learner a fourth
    attempt, and when?" has to stay answerable after the grant stops counting.
    """

    __tablename__ = "qt_additional_attempt_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    #: Caller-supplied and namespaced. Two identical grants a week apart can both be legitimate
    #: and nothing in the domain distinguishes that from a resent form, so only the caller can
    #: say — which is why this key is the one number a client is trusted with.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)

    additional_attempts: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The administrator resolved by the auth seam, never a body field.
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="ck_grant_status"),
        # A grant of zero attempts is not a grant, and a negative one would silently take an
        # attempt away from a learner. Bounded on both sides at the service boundary too.
        CheckConstraint("additional_attempts >= 1", name="ck_grant_additional_attempts"),
        CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) "
            "OR (status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name="ck_grant_revocation_state",
        ),
        Index("ux_grant_idempotency", "idempotency_key", unique=True),
        # The read that decides a learner's entitlement, scoped to all three ids — a query that
        # dropped the course or the quiz would confer an attempt nobody granted.
        Index("ix_grant_scope", "learner_id", "course_id", "quiz_id", "status"),
    )
