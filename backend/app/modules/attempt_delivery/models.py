"""SQLAlchemy ORM models for UC-03 Quiz Attempt Delivery.

All tables are prefixed ``qd_`` so this schema sits alongside UC-01's ``qc_`` and UC-02's ``qb_``
tables without collision.

The schema is the first line of defence for the invariants UC-03 must hold, so
they are expressed as database constraints rather than left to application code:

* ``ux_attempt_single_open`` — a learner can have at most one open attempt per
  quiz, so two concurrent create requests cannot both succeed;
* ``ux_submission_single_success`` — an attempt can have at most one successful
  submission, which makes a duplicate submission impossible even under a race;
* ``ux_submission_idempotency`` — retries of the same logical request collapse onto
  one row;
* ``ck_attempt_commit_state`` — an inconsistent lifecycle (submitted with no
  timestamp, active with a submission reason) is unrepresentable.

Cross-boundary references (``learner_id``, ``course_id``, ``quiz_id``,
``configuration_version_id``, ``question_id``) are intentionally *soft*: they are indexed columns
without foreign keys. Those rows are owned by the platform, UC-01 and UC-02, and UC-03 must survive
them being edited or removed — a configuration version being superseded, a question being retired.
That is a hard requirement, and a cascading foreign key would defeat it. Referential correctness is
enforced at *write* time through the ports, and at *read* time by never depending on the external
row again: the attempt carries a frozen configuration snapshot and each delivered question carries a
frozen question snapshot.

Everything inside UC-03's own aggregate is bound by real foreign keys with cascade deletes.
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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

TABLE_PREFIX = "qd_"


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------


class QuizAttempt(Base):
    """One learner attempt at a quiz.

    Carries a *locked* copy of the UC-01 configuration version — its id, its number
    and the full snapshot — so every later decision for this attempt is made from
    data captured at creation time.
    """

    __tablename__ = "qd_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # Soft references to entities owned outside UC-03.
    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quiz_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Configuration version locking.
    configuration_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    question_presentation: Mapped[str] = mapped_column(String(24), nullable=False)

    #: The attempt this one is a retake of (UC-08). A soft reference like the three above:
    #: lineage for history and analytics, never a rule — the allowance is UC-08's to decide.
    #: NULL on every first attempt, which is what every attempt created before UC-08 was.
    retake_of_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    #: Whether this attempt was sat under UC-09 exam conditions. Recorded here, on the row
    #: UC-03 already owns, because "was this sitting formal?" is a fact about the sitting and
    #: must stay true even if the quiz stops being a formal assessment tomorrow. UC-09 owns
    #: the lifecycle around it; UC-03 owns only this flag.
    is_formal_assessment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    #: Persisted so a randomised selection is reproducible and auditable.
    selection_seed: Mapped[str] = mapped_column(String(64), nullable=False)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Navigation cursor for one-at-a-time delivery; survives reconnection.
    current_position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Server-authoritative timing. A NULL time limit means the attempt is untimed.
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: When the learner (or the timer) committed the attempt.
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: When the submission finished end to end, including downstream hand-off.
    finalised_at: Mapped[datetime | None] = mapped_column(nullable=True)
    submission_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)

    last_activity_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    questions: Mapped[list[AttemptQuestion]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", order_by="AttemptQuestion.position"
    )
    answers: Mapped[list[AttemptAnswer]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )
    flags: Mapped[list[AttemptQuestionFlag]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )
    submissions: Mapped[list[AttemptSubmission]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'SUBMISSION_PENDING', 'SUBMITTED')", name="ck_attempt_status"
        ),
        CheckConstraint(
            "question_presentation IN ('ONE_AT_A_TIME', 'ALL_AT_ONCE')",
            name="ck_attempt_question_presentation",
        ),
        CheckConstraint(
            "submission_reason IS NULL OR "
            "submission_reason IN ('LEARNER_CONFIRMED', 'TIME_EXPIRED', 'DISCONNECT_AUTO_SUBMIT')",
            name="ck_attempt_reason",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_attempt_number"),
        CheckConstraint("total_questions >= 0", name="ck_attempt_total_questions"),
        CheckConstraint("current_position >= 1", name="ck_attempt_current_position"),
        CheckConstraint(
            "time_limit_seconds IS NULL OR time_limit_seconds > 0", name="ck_attempt_time_limit"
        ),
        # An untimed attempt has no expiry; a timed one always does.
        CheckConstraint(
            "(time_limit_seconds IS NULL AND expires_at IS NULL) "
            "OR (time_limit_seconds IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_attempt_expiry",
        ),
        # ACTIVE attempts are never committed; committed attempts always carry an
        # instant and a reason. Makes an inconsistent lifecycle unrepresentable.
        CheckConstraint(
            "(status = 'ACTIVE' AND submitted_at IS NULL AND submission_reason IS NULL "
            "     AND finalised_at IS NULL) "
            "OR (status = 'SUBMISSION_PENDING' AND submitted_at IS NOT NULL "
            "     AND submission_reason IS NOT NULL AND finalised_at IS NULL) "
            "OR (status = 'SUBMITTED' AND submitted_at IS NOT NULL "
            "     AND submission_reason IS NOT NULL AND finalised_at IS NOT NULL)",
            name="ck_attempt_commit_state",
        ),
        # Attempt numbering is unique per learner+quiz, which is what makes the
        # remaining-attempts calculation trustworthy under concurrency.
        UniqueConstraint("learner_id", "quiz_id", "attempt_number", name="ux_attempt_number"),
        # At most one open attempt per learner+quiz, enforced by the database.
        Index(
            "ux_attempt_single_open",
            "learner_id",
            "quiz_id",
            unique=True,
            sqlite_where=text("status IN ('ACTIVE', 'SUBMISSION_PENDING')"),
            postgresql_where=text("status IN ('ACTIVE', 'SUBMISSION_PENDING')"),
        ),
        Index("ix_attempt_learner_quiz", "learner_id", "quiz_id", "status"),
        Index("ix_attempt_learner_course", "learner_id", "course_id"),
        Index("ix_attempt_config_version", "configuration_version_id"),
        # Retake lineage (UC-08) and the formal/standard split UC-10 filters analytics by.
        Index("ix_attempt_retake_of", "retake_of_attempt_id"),
        Index("ix_attempt_formal", "quiz_id", "is_formal_assessment"),
        # Supports sweeping for attempts whose time limit has elapsed.
        Index("ix_attempt_expiry", "status", "expires_at"),
    )


class AttemptQuestion(Base):
    """The frozen question set for an attempt.

    Which questions, in which order, in exactly the shape the learner was shown.
    The snapshot is what keeps the set stable across refreshes and keeps historical
    attempts readable after UC-02 edits or retires a question.
    """

    __tablename__ = "qd_attempt_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qd_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_version: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    question_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    attempt: Mapped[QuizAttempt] = relationship(back_populates="questions")
    answer: Mapped[AttemptAnswer | None] = relationship(
        back_populates="attempt_question", cascade="all, delete-orphan", uselist=False
    )
    flag: Mapped[AttemptQuestionFlag | None] = relationship(
        back_populates="attempt_question", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint(
            "question_type IN ('SINGLE_CHOICE', 'TRUE_FALSE', 'MULTI_SELECT', "
            "'SCENARIO', 'DRAG_TO_ORDER')",
            name="ck_attempt_question_type",
        ),
        CheckConstraint("position >= 1", name="ck_attempt_question_position"),
        CheckConstraint("points >= 0", name="ck_attempt_question_points"),
        # One question per slot, and no question delivered twice in one attempt.
        UniqueConstraint("attempt_id", "position", name="ux_attempt_question_position"),
        UniqueConstraint("attempt_id", "question_id", name="ux_attempt_question_unique"),
        Index("ix_attempt_question_question", "question_id"),
    )


class AttemptAnswer(Base):
    """Current answer state, one row per delivered question.

    Answers are upserted, so the row always holds the latest successfully persisted
    response. ``revision`` advances only when the canonical response actually
    changes, which is what makes a repeated autosave idempotent.
    """

    __tablename__ = "qd_attempt_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qd_attempts.id", ondelete="CASCADE"), nullable=False
    )
    attempt_question_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qd_attempt_questions.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)

    answered: Mapped[bool] = mapped_column(Boolean(create_constraint=False), nullable=False)
    #: True when the stored response fully answers the question. For every type
    #: except SCENARIO this equals ``answered``; a SCENARIO may hold a partially
    #: completed set of sub-answers, which must still be persisted for autosave but
    #: must not count as complete at submission time.
    complete: Mapped[bool] = mapped_column(
        Boolean(create_constraint=False), nullable=False, default=False
    )

    #: Canonical response payload; SQL NULL when the answer has been cleared.
    #: `none_as_null` is essential: without it SQLAlchemy would store the JSON value
    #: `null`, which is not SQL NULL, and ck_answer_payload would reject a cleared answer.
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: SHA-256 of the canonical payload, used to detect a no-op save.
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    first_saved_at: Mapped[datetime] = mapped_column(nullable=False)
    saved_at: Mapped[datetime] = mapped_column(nullable=False)

    attempt: Mapped[QuizAttempt] = relationship(back_populates="answers")
    attempt_question: Mapped[AttemptQuestion] = relationship(back_populates="answer")

    __table_args__ = (
        CheckConstraint("answered IN (0, 1)", name="ck_answer_answered"),
        CheckConstraint("source IN ('MANUAL', 'AUTOSAVE', 'SYSTEM')", name="ck_answer_source"),
        CheckConstraint("revision >= 1", name="ck_answer_revision"),
        # "Answered" and "has a response" can never disagree.
        CheckConstraint(
            "(answered = 1 AND response IS NOT NULL) OR (answered = 0 AND response IS NULL)",
            name="ck_answer_payload",
        ),
        # Nothing can be complete without being answered.
        CheckConstraint(
            "complete IN (0, 1) AND (complete = 0 OR answered = 1)", name="ck_answer_complete"
        ),
        UniqueConstraint(
            "attempt_id", "attempt_question_id", name="ux_answer_per_attempt_question"
        ),
        UniqueConstraint("attempt_id", "question_id", name="ux_answer_per_question"),
    )


class AttemptAnswerRevision(Base):
    """Append-only audit of every accepted save.

    Gives operational evidence that an autosave landed, and lets a support engineer
    reconstruct what the learner had entered at any point during the attempt.
    """

    __tablename__ = "qd_attempt_answer_revisions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qd_attempts.id", ondelete="CASCADE"), nullable=False
    )
    attempt_question_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qd_attempt_questions.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    answered: Mapped[bool] = mapped_column(Boolean(create_constraint=False), nullable=False)
    complete: Mapped[bool] = mapped_column(
        Boolean(create_constraint=False), nullable=False, default=False
    )
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    saved_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint("answered IN (0, 1)", name="ck_answer_revision_answered"),
        CheckConstraint(
            "source IN ('MANUAL', 'AUTOSAVE', 'SYSTEM')", name="ck_answer_revision_source"
        ),
        UniqueConstraint("attempt_question_id", "revision", name="ux_answer_revision"),
        Index("ix_answer_revision_attempt", "attempt_id", "saved_at"),
    )


class AttemptQuestionFlag(Base):
    """Flag ("review later") state, persisted so it survives reconnection."""

    __tablename__ = "qd_attempt_question_flags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qd_attempts.id", ondelete="CASCADE"), nullable=False
    )
    attempt_question_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qd_attempt_questions.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean(create_constraint=False), nullable=False)
    flagged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    attempt: Mapped[QuizAttempt] = relationship(back_populates="flags")
    attempt_question: Mapped[AttemptQuestion] = relationship(back_populates="flag")

    __table_args__ = (
        CheckConstraint("flagged IN (0, 1)", name="ck_flag_value"),
        CheckConstraint("(flagged = 1) = (flagged_at IS NOT NULL)", name="ck_flag_instant"),
        UniqueConstraint("attempt_id", "attempt_question_id", name="ux_flag_per_attempt_question"),
        Index("ix_flag_attempt_flagged", "attempt_id", "flagged"),
    )


class AttemptSubmission(Base):
    """One row per logical submission request, keyed by idempotency key.

    This table is what makes double-clicks, network retries and pending-retry flows
    safe. ``ux_submission_idempotency`` collapses retries of the same request, and
    ``ux_submission_single_success`` permits at most one SUBMITTED row per attempt.
    """

    __tablename__ = "qd_attempt_submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qd_attempts.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    submission_reason: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    answered_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Response body replayed for an idempotent repeat of a completed submission.
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    downstream_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(nullable=False)
    last_attempted_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    attempt: Mapped[QuizAttempt] = relationship(back_populates="submissions")

    __table_args__ = (
        CheckConstraint("state IN ('PENDING', 'SUBMITTED', 'FAILED')", name="ck_submission_state"),
        CheckConstraint(
            "submission_reason IN ('LEARNER_CONFIRMED', 'TIME_EXPIRED')",
            name="ck_submission_reason",
        ),
        CheckConstraint("attempt_count >= 1", name="ck_submission_attempt_count"),
        CheckConstraint(
            "answered_count >= 0 AND total_questions >= 0", name="ck_submission_counts"
        ),
        CheckConstraint(
            "(state = 'SUBMITTED') = (completed_at IS NOT NULL)", name="ck_submission_completed"
        ),
        UniqueConstraint("attempt_id", "idempotency_key", name="ux_submission_idempotency"),
        # The hard guarantee: at most one successful submission per attempt.
        Index(
            "ux_submission_single_success",
            "attempt_id",
            unique=True,
            sqlite_where=text("state = 'SUBMITTED'"),
            postgresql_where=text("state = 'SUBMITTED'"),
        ),
        Index("ix_submission_state", "state", "last_attempted_at"),
    )
