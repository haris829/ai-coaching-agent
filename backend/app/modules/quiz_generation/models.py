"""SQLAlchemy models for generated quizzes.

Tables are prefixed ``qz_`` — the next unclaimed pair, since ``qc_`` (configuration), ``qg_``
(gating) and ``qk_`` (coaching) are taken.

WHY THESE TWO TABLES EXIST AT ALL
---------------------------------
The company asked for a deliberately small contract:

    POST /quiz     { topic, count }        -> { quizId, questions + keys, passMark }
    POST /results  { quizId, answers }     -> { pass | fail }

That is stateless from their side, and needs exactly one thing remembered between the two calls:
**which questions this quiz asked, in which order.** Without it the second call cannot mark
anything, and trusting the client to send the questions back would mean marking against whatever
a caller chose to submit.

So a generated quiz is a *set of question references plus a pass mark*, and nothing else. The
questions themselves stay in UC-02's question bank where they are versioned, snapshotted and
validated — these tables hold no question text and no answer key.

WHAT THIS IS NOT
----------------
Not a replacement for UC-01's configuration versions or UC-03's attempts. Those model a learner
sitting a paper under locked rules, with autosave, timing, resumption and idempotent submission. A
richer integration should use them. This is the thin contract the company asked for, and it is
deliberately thinner: one call out, one call back, a verdict.

The pass mark is **frozen onto the quiz row** at generation time. A quiz already sat must not be
re-marked against a threshold somebody changed afterwards — the same reason UC-04 freezes the pass
mark it scored against.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, id_column

TABLE_PREFIX = "qz_"

#: The company's stated threshold: 50% passes. Frozen per quiz — see the module docstring.
DEFAULT_PASS_MARK = 50.0


class GeneratedQuiz(Base):
    """One generated quiz: what it was about, how it is marked, and when it was made."""

    __tablename__ = f"{TABLE_PREFIX}generated_quizzes"

    id: Mapped[str] = id_column()

    #: What the questions were generated about. Free text, because a caller may ask by topic
    #: ("OOP in Java") rather than by course.
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The course this was generated for, when one was named. A soft reference to
    #: ``qc_courses.code`` — not a foreign key, so a quiz survives the catalogue being reimported.
    course_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: How many questions were asked for, and how many survived validation. Both are kept: a quiz
    #: that asked for 20 and stored 17 is a fact worth being able to see later.
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The percentage needed to pass, frozen at generation.
    pass_mark: Mapped[float] = mapped_column(Float, nullable=False, default=DEFAULT_PASS_MARK)

    #: Which model produced it. Operational provenance: "which model wrote this question" is the
    #: first thing anyone asks when a question turns out to be wrong.
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    questions: Mapped[list[GeneratedQuizQuestion]] = relationship(
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="GeneratedQuizQuestion.sequence",
    )

    __table_args__ = (
        CheckConstraint("requested_count >= 1", name="requested_count_positive"),
        CheckConstraint("question_count >= 0", name="question_count_non_negative"),
        CheckConstraint("question_count <= requested_count", name="count_within_request"),
        CheckConstraint("pass_mark >= 0 AND pass_mark <= 100", name="pass_mark_range"),
        Index(f"ix_{TABLE_PREFIX}generated_quizzes_course_ref", "course_ref"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GeneratedQuiz {self.id} {self.topic!r} {self.question_count}q>"


class GeneratedQuizQuestion(Base):
    """One question's place in a generated quiz.

    A reference, not a copy. The text, the options and the answer key live in UC-02's question bank;
    duplicating them here would create a second version of a question that could disagree with the
    first.
    """

    __tablename__ = f"{TABLE_PREFIX}generated_quiz_questions"

    id: Mapped[str] = id_column()
    quiz_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}generated_quizzes.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Soft reference to ``qb_questions.id``.
    question_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 1-based position, so ``Q1`` in the caller's answer payload means something definite.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    quiz: Mapped[GeneratedQuiz] = relationship(back_populates="questions")

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        # One question per slot, and no question asked twice in one quiz.
        UniqueConstraint("quiz_id", "sequence", name="quiz_id_sequence"),
        UniqueConstraint("quiz_id", "question_id", name="quiz_id_question_id"),
        Index(f"ix_{TABLE_PREFIX}generated_quiz_questions_question_id", "question_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GeneratedQuizQuestion {self.quiz_id}#{self.sequence}>"
