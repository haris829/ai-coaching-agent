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
    Boolean,
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


class QuizSubmission(Base):
    """One sitting of a generated quiz: what was answered, and what the verdict was.

    WHY THE VERDICT IS STORED AND NOT ONLY RETURNED
    -----------------------------------------------
    A pass that exists only in an HTTP response is not a record of anything. If somebody later asks
    "did this person pass, and on what", the answer has to come from a row rather than from whoever
    still has the browser tab open. So the arithmetic is stored with the numbers it was computed
    from — total, correct, percentage, and the pass mark **as it stood at the time**.

    The pass mark is copied here rather than read back through the quiz. It is already frozen on the
    quiz row, so this is belt and braces, but it makes a stored result readable on its own: a row
    that says 55% against a pass mark of 50 needs no second lookup to be understood, and cannot be
    re-interpreted by a later change to anything.

    WHY THIS IS NOT UC-04
    ---------------------
    UC-04 records the immutable result of a *submitted attempt* — one locked to a configuration
    version, timed, with a snapshot of the exact questions delivered — and that result drives
    certification. This records a sitting of the thin two-call contract, which has no attempt, no
    timing and no certificate behind it. Keeping them in separate tables is what stops an anonymous
    caller minting scored attempts for learners who never sat anything.
    """

    __tablename__ = f"{TABLE_PREFIX}quiz_submissions"

    id: Mapped[str] = id_column()
    quiz_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}generated_quizzes.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Who sat it, when the caller was identified. Nullable because the contract does not require a
    #: learner — it is a quiz id and a set of answers.
    learner_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    total: Mapped[int] = mapped_column(Integer, nullable=False)
    correct: Mapped[int] = mapped_column(Integer, nullable=False)
    percentage: Mapped[float] = mapped_column(Float, nullable=False)
    #: The pass mark this sitting was judged against — see the class docstring.
    pass_mark: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)

    submitted_at: Mapped[datetime] = mapped_column(nullable=False)

    answers: Mapped[list[SubmittedAnswer]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmittedAnswer.sequence",
    )

    __table_args__ = (
        CheckConstraint("total >= 0", name="total_non_negative"),
        CheckConstraint("correct >= 0 AND correct <= total", name="correct_within_total"),
        CheckConstraint("percentage >= 0 AND percentage <= 100", name="percentage_range"),
        CheckConstraint("pass_mark >= 0 AND pass_mark <= 100", name="pass_mark_range"),
        Index(f"ix_{TABLE_PREFIX}quiz_submissions_quiz_id", "quiz_id"),
        Index(f"ix_{TABLE_PREFIX}quiz_submissions_learner_ref", "learner_ref"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        verdict = "PASS" if self.passed else "FAIL"
        return f"<QuizSubmission {self.id} {verdict} {self.percentage}%>"


class SubmittedAnswer(Base):
    """One answer as submitted, and whether it was right.

    ``given_label`` is nullable: a question left unanswered is stored as an answer of nothing,
    marked incorrect. Recording the absence is the point — a row per question means the stored
    sitting accounts for every question asked, so nobody can later argue a question was never put.

    The correct label is deliberately **not** copied here. It lives on the question in UC-02's bank,
    which is where marking reads it from; a second copy could disagree with the first, and the one
    that disagreed would be the one somebody quoted.
    """

    __tablename__ = f"{TABLE_PREFIX}submitted_answers"

    id: Mapped[str] = id_column()
    submission_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}quiz_submissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Soft reference to ``qb_questions.id``.
    question_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The option label the caller chose, or ``None`` for a question left unanswered.
    given_label: Mapped[str | None] = mapped_column(String(4), nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)

    submission: Mapped[QuizSubmission] = relationship(back_populates="answers")

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        UniqueConstraint("submission_id", "sequence", name="submission_id_sequence"),
        Index(f"ix_{TABLE_PREFIX}submitted_answers_question_id", "question_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SubmittedAnswer {self.submission_id}#{self.sequence}={self.given_label}>"
