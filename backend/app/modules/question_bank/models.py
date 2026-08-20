"""SQLAlchemy models for UC-02 Question Bank Management.

All tables are prefixed ``qb_`` so this schema merges into the larger Courses Quiz Agent
database without colliding with another module's tables.

Design notes
------------
* **Structured, not stringly-typed.** Options are rows in ``qb_question_options`` and topics
  are a proper many-to-many via ``qb_question_topics`` — never comma-separated text.
* **Historical preservation.** ``QuestionSnapshot`` freezes one immutable version of a
  question; ``QuestionUsage`` links an attempt to the snapshot it was actually delivered. An
  edit or a retirement can therefore never rewrite a completed attempt's history.
* **No hard delete of history.** ``QuestionUsage`` uses ``RESTRICT`` against both the question
  and the snapshot, so the database itself refuses to destroy a question that has usage.
* **Portable types only.** See ``app/db/base.py`` for why enum-like columns are ``String``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    TABLE_PREFIX,
    Base,
    created_at_column,
    id_column,
    updated_at_column,
)
from app.modules.question_bank.domain.enums import (
    AttemptStatus,
    ImportStatus,
    QuestionStatus,
)


class Sequence(Base):
    """Portable monotonic counter.

    SQLite cannot use an autoincrement column outside the primary key, so human-readable
    question references are allocated from this table inside the same transaction that creates
    the question. Works identically on every candidate database.
    """

    __tablename__ = f"{TABLE_PREFIX}sequences"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = updated_at_column()


class Question(Base):
    """A question in the bank. Never hard-deleted once it has historical usage."""

    __tablename__ = f"{TABLE_PREFIX}questions"

    id: Mapped[str] = id_column()

    #: Monotonic sequence allocated from ``qb_sequences``. Stable for the question's whole life.
    seq: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    #: Human-readable identity shown in the admin UI and in historical reports (e.g. Q-000042).
    #: Never reassigned — including after retirement.
    reference: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    #: Optional stable key from a source system. Used for CSV de-duplication.
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)

    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=QuestionStatus.ACTIVE.value
    )

    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Long-form vignette. Required for SCENARIO questions, NULL for every other type.
    scenario_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ---- Scoring metadata ----
    points: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    scoring_strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    penalty_per_incorrect: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    #: Bumped on every content-changing edit; each version has an immutable snapshot.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: SHA-256 of the normalised content. Drives duplicate detection.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retired_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    #: Provenance when the question arrived through a CSV bulk import.
    import_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}question_imports.id", ondelete="SET NULL"),
        nullable=True,
    )
    import_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    options: Mapped[list[QuestionOption]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionOption.position",
        lazy="selectin",
    )
    topic_links: Mapped[list[QuestionTopic]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    snapshots: Mapped[list[QuestionSnapshot]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionSnapshot.version",
        lazy="selectin",
    )
    usages: Mapped[list[QuestionUsage]] = relationship(
        back_populates="question",
        # NO cascade delete: usage is history. The FK is RESTRICT.
        passive_deletes="all",
        lazy="noload",
    )
    import_run: Mapped[QuestionImport | None] = relationship(
        back_populates="questions", lazy="noload"
    )

    __table_args__ = (
        Index(f"ix_{TABLE_PREFIX}questions_status", "status"),
        Index(f"ix_{TABLE_PREFIX}questions_type", "type"),
        Index(f"ix_{TABLE_PREFIX}questions_status_type", "status", "type"),
        Index(f"ix_{TABLE_PREFIX}questions_content_hash", "content_hash"),
        Index(f"ix_{TABLE_PREFIX}questions_created_at", "created_at"),
        Index(f"ix_{TABLE_PREFIX}questions_import_id", "import_id"),
        CheckConstraint("points > 0", name="questions_points_positive"),
        CheckConstraint("penalty_per_incorrect >= 0", name="questions_penalty_non_negative"),
        CheckConstraint("version >= 1", name="questions_version_positive"),
    )

    @property
    def topics(self) -> list[Topic]:
        return [link.topic for link in self.topic_links]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Question {self.reference} {self.type} {self.status}>"


class QuestionOption(Base):
    """An answer option (choice types) or an orderable item (DRAG_TO_ORDER).

    ``position`` and ``correct_position`` are deliberately separate columns: the first is the
    default *presentation* order (delivery may shuffle it), the second is the *correct answer*
    order. Conflating them would destroy the answer key for drag-to-order questions.
    """

    __tablename__ = f"{TABLE_PREFIX}question_options"

    id: Mapped[str] = id_column()
    question_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}questions.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Stable key within the question: "A".."D", "TRUE"/"FALSE", or an item key.
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    #: Authoring order == DEFAULT PRESENTATION ORDER.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Choice types: is this option a correct selection?
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: SCENARIO: marks the single primary answer.
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: DRAG_TO_ORDER: 1-based rank in the CORRECT ANSWER ORDER. NULL for every other type.
    correct_position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    question: Mapped[Question] = relationship(back_populates="options")

    __table_args__ = (
        UniqueConstraint("question_id", "label", name="uq_question_options_question_id_label"),
        UniqueConstraint(
            "question_id", "position", name="uq_question_options_question_id_position"
        ),
        # NULLs are distinct in every supported database, so this constrains only
        # DRAG_TO_ORDER rows — exactly the intent.
        UniqueConstraint(
            "question_id",
            "correct_position",
            name="uq_question_options_question_id_correct_position",
        ),
        Index(f"ix_{TABLE_PREFIX}question_options_question_id", "question_id"),
        CheckConstraint("position >= 1", name="question_options_position_positive"),
        CheckConstraint(
            "correct_position IS NULL OR correct_position >= 1",
            name="correct_position_positive",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QuestionOption {self.label} pos={self.position} correct={self.is_correct}>"


class Topic(Base):
    """A reusable topic tag."""

    __tablename__ = f"{TABLE_PREFIX}topics"

    id: Mapped[str] = id_column()
    slug: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    question_links: Mapped[list[QuestionTopic]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", lazy="noload"
    )

    __table_args__ = (Index(f"ix_{TABLE_PREFIX}topics_is_active", "is_active"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Topic {self.slug}>"


class QuestionTopic(Base):
    """Many-to-many join between questions and topics."""

    __tablename__ = f"{TABLE_PREFIX}question_topics"

    question_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}questions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}topics.id", ondelete="CASCADE"),
        primary_key=True,
    )
    assigned_at: Mapped[datetime] = created_at_column()
    assigned_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    question: Mapped[Question] = relationship(back_populates="topic_links")
    topic: Mapped[Topic] = relationship(back_populates="question_links", lazy="joined")

    __table_args__ = (Index(f"ix_{TABLE_PREFIX}question_topics_topic_id", "topic_id"),)


class QuestionSnapshot(Base):
    """Immutable frozen representation of ONE version of a question.

    This is the mechanism that makes historical reporting safe (UC-02 §16): an attempt
    references the snapshot it was delivered, so later edits or retirement can never rewrite
    history. Rows here are written once and never updated.
    """

    __tablename__ = f"{TABLE_PREFIX}question_snapshots"

    id: Mapped[str] = id_column()
    question_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Denormalised so a report can be rendered without joining the live question at all.
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    points: Mapped[float] = mapped_column(Float, nullable=False)
    scoring_strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    penalty_per_incorrect: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Full frozen JSON: options (label/text/position/correctness/correct_position) + topics.
    payload: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = created_at_column()
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    question: Mapped[Question] = relationship(back_populates="snapshots")
    usages: Mapped[list[QuestionUsage]] = relationship(
        back_populates="snapshot", passive_deletes="all", lazy="noload"
    )

    __table_args__ = (
        UniqueConstraint(
            "question_id", "version", name="uq_question_snapshots_question_id_version"
        ),
        Index(f"ix_{TABLE_PREFIX}question_snapshots_question_id", "question_id"),
        Index(f"ix_{TABLE_PREFIX}question_snapshots_content_hash", "content_hash"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QuestionSnapshot {self.reference} v{self.version}>"


class QuestionUsage(Base):
    """Records that a question snapshot was delivered to a quiz attempt.

    INTEGRATION SEAM
    ----------------
    ``attempt_ref`` is an opaque identifier owned by the quiz-delivery / attempt module, which
    is implemented outside UC-02. It is deliberately NOT a foreign key, so this module carries
    no build-time dependency on another team's unfinished attempt tables. When the modules
    merge, a single FK to the real attempt table can be added against this column — see
    ``docs/INTEGRATION.md``.
    """

    __tablename__ = f"{TABLE_PREFIX}question_usages"

    id: Mapped[str] = id_column()

    attempt_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    learner_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    question_id: Mapped[str] = mapped_column(
        String(36),
        # RESTRICT: the database itself refuses to destroy a question that has history.
        ForeignKey(f"{TABLE_PREFIX}questions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}question_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 1-based position of this question within the attempt, fixed when the attempt was created.
    #: Locking the order here is what stops a learner's question sequence shifting mid-attempt.
    #: NULL for usages recorded by a caller that does not order its questions.
    delivery_position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    attempt_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AttemptStatus.IN_PROGRESS.value
    )

    #: JSON learner response, shaped per question type.
    learner_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: JSON array of option labels in the order they were SHOWN to the learner. Kept separate
    #: from the snapshot's correct order.
    presentation_order: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    awarded_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_points: Mapped[float | None] = mapped_column(Float, nullable=True)

    delivered_at: Mapped[datetime] = created_at_column()
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    question: Mapped[Question] = relationship(back_populates="usages")
    snapshot: Mapped[QuestionSnapshot] = relationship(back_populates="usages", lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "attempt_ref", "question_id", name="uq_question_usages_attempt_ref_question_id"
        ),
        # No two questions share a position in one attempt. NULLs are distinct in every
        # supported database, so this constrains only callers that do order their questions.
        UniqueConstraint(
            "attempt_ref",
            "delivery_position",
            name="uq_question_usages_attempt_ref_delivery_position",
        ),
        Index(f"ix_{TABLE_PREFIX}question_usages_question_id", "question_id"),
        Index(f"ix_{TABLE_PREFIX}question_usages_attempt_ref", "attempt_ref"),
        Index(f"ix_{TABLE_PREFIX}question_usages_attempt_status", "attempt_status"),
        CheckConstraint(
            "delivery_position IS NULL OR delivery_position >= 1",
            name="delivery_position_positive",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QuestionUsage attempt={self.attempt_ref} q={self.question_id}>"


class QuestionImport(Base):
    """One CSV bulk-import run and its aggregate outcome (UC-02 §19)."""

    __tablename__ = f"{TABLE_PREFIX}question_imports"

    id: Mapped[str] = id_column()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ImportStatus.PROCESSING.value
    )

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Set when the whole file could not be processed (e.g. missing headers).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    started_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    questions: Mapped[list[Question]] = relationship(back_populates="import_run", lazy="noload")
    errors: Mapped[list[QuestionImportError]] = relationship(
        back_populates="import_run",
        cascade="all, delete-orphan",
        order_by="QuestionImportError.row_number",
        lazy="selectin",
    )

    __table_args__ = (
        Index(f"ix_{TABLE_PREFIX}question_imports_status", "status"),
        Index(f"ix_{TABLE_PREFIX}question_imports_started_at", "started_at"),
        CheckConstraint("total_rows >= 0", name="total_rows_non_negative"),
        CheckConstraint("imported_rows >= 0", name="imported_rows_non_negative"),
        CheckConstraint("rejected_rows >= 0", name="rejected_rows_non_negative"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<QuestionImport {self.filename} {self.status} "
            f"{self.imported_rows}/{self.total_rows}>"
        )


class QuestionImportError(Base):
    """A field-level reason a CSV row was rejected.

    Multiple rows per import, and multiple errors per row — an admin sees every problem with a
    row at once, not just the first.
    """

    __tablename__ = f"{TABLE_PREFIX}question_import_errors"

    id: Mapped[str] = id_column()
    import_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}question_imports.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 1-based row number as the admin sees it in a spreadsheet (header is row 1).
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: The original row as JSON, so an admin can see exactly what was rejected.
    raw_row: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = created_at_column()

    import_run: Mapped[QuestionImport] = relationship(back_populates="errors")

    __table_args__ = (
        Index(f"ix_{TABLE_PREFIX}question_import_errors_import_id", "import_id"),
        Index(
            f"ix_{TABLE_PREFIX}question_import_errors_import_id_row_number",
            "import_id",
            "row_number",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QuestionImportError row={self.row_number} {self.code}>"
