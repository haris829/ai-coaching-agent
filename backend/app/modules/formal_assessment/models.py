"""SQLAlchemy ORM models for UC-09 Formal Assessment Mode.

Tables are prefixed ``qs_`` (quiz *supervision*) so this schema sits alongside UC-01's ``qc_``,
UC-02's ``qb_``, UC-03's ``qd_``, UC-04's ``qr_``, UC-05's ``qg_``, UC-06's ``qf_``, UC-07's
``qk_`` and UC-08's ``qt_``.

UC-09 shipped with no schema — persistence was three Protocols, and ``docs/INTEGRATION.md`` §4
listed the three tables that would satisfy them. This file is that section carried out. No domain
rule, service or test of the formal-assessment logic changed to make it possible; the in-memory
binding they were written against is still in ``repositories/`` and still implements the same
contract.

**Four guarantees live in these indexes, not in application code.** Each closes a race that a
read-then-write cannot:

``ux_formal_attempt_open``
    One *open* formal attempt per learner and quiz. Partial, so a later sitting is unaffected by
    a finished one.

``ux_formal_attempt_upstream``
    One formal record per UC-03 attempt — a second would mean two lifecycles supervising one
    sitting.

``ux_device_session_active``
    **The single-device lock.** One ACTIVE session per formal attempt, claimed by an insert rather
    than by a check: of two simultaneous starts exactly one wins, and it wins before any attempt is
    created.

``ux_formal_review_attempt``
    One review — and therefore one queue entry, and one certificate decision — per formal attempt.

**Compare-and-set on every update.** Each row carries a ``version``; a write applies only if the
stored version is one behind the one the record was read at. That single condition is what makes
the duplicate-submission, duplicate-disconnect, duplicate-decision and duplicate-certificate races
resolve to one winner instead of two.

**Nothing here is ever deleted** — not a formal attempt, not a session (including a rejected one),
not a review. "Which device sat this assessment?" and "who approved this certificate?" have to stay
answerable.

Cross-boundary references (``learner_id``, ``course_id``, ``quiz_id``, ``attempt_id``,
``configuration_version_id``, ``result_id``) are **soft**: indexed columns without foreign keys,
following UC-03's precedent. UC-09's own three tables are bound to each other by real foreign keys.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.modules.formal_assessment.domain.enums import (
    OPEN_FORMAL_STATES,
    AssessorDecision,
    DeviceSessionState,
    FormalAttemptState,
    FormalSubmissionReason,
    QueuePublishState,
    ReviewState,
)

TABLE_PREFIX = "qs_"


def _values(enum_class: type) -> str:
    """``'A', 'B'`` — for a CHECK constraint built from the domain's own vocabulary.

    Derived rather than typed out, so adding a state to the enum and forgetting the constraint is
    not possible: the two cannot drift because there is only one list.
    """
    return ", ".join(f"'{member.value}'" for member in enum_class)


#: The states in which a formal attempt still holds the learner's one open slot. Taken from the
#: domain constant so the partial index and the state machine cannot disagree about what "open"
#: means — the whole point of the index is to enforce the domain's rule, not a copy of it.
_OPEN_STATE_VALUES = ", ".join(f"'{state.value}'" for state in sorted(OPEN_FORMAL_STATES))


class FormalAttemptRow(Base):
    """One supervised sitting: conditions, identity, device, lifecycle, result and gate."""

    __tablename__ = f"{TABLE_PREFIX}formal_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)

    state: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    #: The UC-03 attempt this supervises. NULL until the learner has acknowledged the conditions,
    #: confirmed their identity and claimed a device — the attempt is created last, on purpose.
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    configuration_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Set when this formal sitting is a UC-08 retake of an earlier one.
    retake_of_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ---- conditions (§2) --------------------------------------------------
    conditions_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conditions_acknowledged_codes: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    conditions_acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    conditions_user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- identity (§3) ----------------------------------------------------
    identity_confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    identity_email_confirmed: Mapped[bool | None] = mapped_column(
        Boolean(create_constraint=False), nullable=True
    )
    identity_email_supplied: Mapped[bool | None] = mapped_column(
        Boolean(create_constraint=False), nullable=True
    )
    identity_rejected_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: Rejections since the last successful confirmation, kept separately from the total so the
    #: audit trail keeps both "how many times did this learner mistype their name?" and "how many
    #: in the run that eventually succeeded?".
    pending_identity_rejections: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    device_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    submission_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ---- disconnect (§8) --------------------------------------------------
    disconnect_detected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    disconnect_reported_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    disconnect_last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    disconnect_autosaved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    disconnect_answered_questions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disconnect_total_questions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disconnect_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auto_submit_started_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # ---- result, carried verbatim from UC-04 and UC-05 --------------------
    result_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result_passed: Mapped[bool | None] = mapped_column(
        Boolean(create_constraint=False), nullable=True
    )
    result_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_pass_mark: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_total_marks: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_maximum_marks: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_score_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_calculated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    review_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ---- the certificate gate (§11) ---------------------------------------
    certificate_workflow_triggered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    certificate_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)

    anomalies: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    #: Compare-and-set token. See the module docstring.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    sessions: Mapped[list[DeviceSessionRow]] = relationship(
        back_populates="formal_attempt", cascade="all, delete-orphan"
    )
    review: Mapped[FormalReviewRow | None] = relationship(
        back_populates="formal_attempt", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint(f"state IN ({_values(FormalAttemptState)})", name="ck_formal_state"),
        CheckConstraint(
            "submission_reason IS NULL OR "
            f"submission_reason IN ({_values(FormalSubmissionReason)})",
            name="ck_formal_submission_reason",
        ),
        CheckConstraint("identity_rejected_attempts >= 0", name="ck_formal_identity_rejections"),
        CheckConstraint("pending_identity_rejections >= 0", name="ck_formal_pending_rejections"),
        CheckConstraint("version >= 1", name="ck_formal_version"),
        # A submitted formal attempt always carries both the instant and the reason. Makes the
        # half-committed lifecycle that a crashed auto-submit could otherwise leave unrepresentable.
        CheckConstraint(
            "(submitted_at IS NULL AND submission_reason IS NULL) "
            "OR (submitted_at IS NOT NULL AND submission_reason IS NOT NULL)",
            name="ck_formal_submission_pair",
        ),
        # The certificate gate's own record: a triggered workflow always names when it fired.
        CheckConstraint(
            "certificate_reference IS NULL OR certificate_workflow_triggered_at IS NOT NULL",
            name="ck_formal_certificate_pair",
        ),
        # One OPEN formal attempt per learner and quiz. Partial: a finished sitting must not block
        # the next one.
        Index(
            "ux_formal_attempt_open",
            "learner_id",
            "quiz_id",
            unique=True,
            sqlite_where=text(f"state IN ({_OPEN_STATE_VALUES})"),
            postgresql_where=text(f"state IN ({_OPEN_STATE_VALUES})"),
        ),
        # One formal record per UC-03 attempt.
        Index(
            "ux_formal_attempt_upstream",
            "attempt_id",
            unique=True,
            sqlite_where=text("attempt_id IS NOT NULL"),
            postgresql_where=text("attempt_id IS NOT NULL"),
        ),
        Index("ux_formal_attempt_idempotency", "idempotency_key", unique=True),
        # The AI-coaching check runs on *every* coaching request, so it gets its own index: the
        # question "is any formal assessment of this learner's in progress?" must stay cheap.
        Index("ix_formal_attempt_learner_state", "learner_id", "state"),
        Index("ix_formal_attempt_quiz", "quiz_id", "state"),
    )


class DeviceSessionRow(Base):
    """One device's claim on a formal attempt. The single-device lock lives in its index."""

    __tablename__ = f"{TABLE_PREFIX}formal_device_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    formal_attempt_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            f"{TABLE_PREFIX}formal_attempts.id",
            ondelete="CASCADE",
            # Named explicitly; the composed name is 65 characters, past PostgreSQL's limit.
            name="fk_qs_device_sessions_formal_attempt_id",
        ),
        nullable=False,
    )
    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)

    #: A server-issued credential. The client's fingerprint below is *evidence*, recorded for the
    #: audit trail, and decides nothing — a device that could authenticate itself by describing
    #: itself would not be a lock.
    session_token: Mapped[str] = mapped_column(String(128), nullable=False)

    registered_at: Mapped[datetime] = mapped_column(nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    superseded_by_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    client_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    device_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_platform: Mapped[str | None] = mapped_column(String(64), nullable=True)

    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    formal_attempt: Mapped[FormalAttemptRow] = relationship(back_populates="sessions")

    __table_args__ = (
        CheckConstraint(f"state IN ({_values(DeviceSessionState)})", name="ck_device_state"),
        CheckConstraint("version >= 1", name="ck_device_version"),
        CheckConstraint(
            "(state = 'ACTIVE' AND closed_at IS NULL) OR (state <> 'ACTIVE')",
            name="ck_device_active_not_closed",
        ),
        # THE SINGLE-DEVICE LOCK. One ACTIVE session per formal attempt, claimed by an insert.
        Index(
            "ux_device_session_active",
            "formal_attempt_id",
            unique=True,
            sqlite_where=text("state = 'ACTIVE'"),
            postgresql_where=text("state = 'ACTIVE'"),
        ),
        # A retried registration from the same client resolves to the session it already holds,
        # rather than being refused as a second device.
        Index(
            "ux_device_session_request",
            "formal_attempt_id",
            "client_request_id",
            unique=True,
            sqlite_where=text("client_request_id IS NOT NULL"),
            postgresql_where=text("client_request_id IS NOT NULL"),
        ),
        Index("ix_device_session_learner", "learner_id", "state"),
    )


class FormalReviewRow(Base):
    """One assessor review. Persisted *before* the queue is touched — see the class docstring."""

    __tablename__ = f"{TABLE_PREFIX}formal_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    formal_attempt_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}formal_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)

    state: Mapped[str] = mapped_column(String(32), nullable=False)

    percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    auto_submitted: Mapped[bool] = mapped_column(
        Boolean(create_constraint=False),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    anomaly_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_started_at: Mapped[datetime | None] = mapped_column(nullable=True)

    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- queue publication, which is bookkeeping and never a gate ---------
    publish_state: Mapped[str] = mapped_column(String(16), nullable=False)
    publish_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_publish_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_publish_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    formal_attempt: Mapped[FormalAttemptRow] = relationship(back_populates="review")

    __table_args__ = (
        CheckConstraint(f"state IN ({_values(ReviewState)})", name="ck_review_state"),
        CheckConstraint(
            f"decision IS NULL OR decision IN ({_values(AssessorDecision)})",
            name="ck_review_decision",
        ),
        CheckConstraint(
            f"publish_state IN ({_values(QueuePublishState)})", name="ck_review_publish_state"
        ),
        CheckConstraint("publish_attempts >= 0", name="ck_review_publish_attempts"),
        CheckConstraint("anomaly_count >= 0", name="ck_review_anomaly_count"),
        CheckConstraint("version >= 1", name="ck_review_version"),
        # A decision always names the human who made it and when. An approval nobody is
        # accountable for is exactly what human review exists to prevent, so it is not
        # representable.
        CheckConstraint(
            "(decision IS NULL AND decided_by IS NULL AND decided_at IS NULL) "
            "OR (decision IS NOT NULL AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_review_decision_attribution",
        ),
        # One review — one queue entry, one decision, one certificate — per formal attempt.
        Index("ux_formal_review_attempt", "formal_attempt_id", unique=True),
        # The assessor queue, and the recovery sweep over reviews the queue never accepted.
        Index("ix_formal_review_pending", "state", "created_at"),
        Index("ix_formal_review_publish", "publish_state", "created_at"),
        Index("ix_formal_review_assignee", "assigned_to", "state"),
    )
