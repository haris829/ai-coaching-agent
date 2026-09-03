"""The two operations the company's contract needs: make a quiz, then mark it.

    create(topic, count)          -> a quiz, its questions, its answer key, its pass mark
    mark(quiz_id, answers)        -> pass or fail

MARKING IS NOT SCORING
----------------------
UC-04 marks a *submitted attempt* against the answer key of the exact question versions delivered,
records an immutable result and drives certification. That is the real thing, and it is untouched.

This marks a set of answers against a set of questions and returns a verdict. It exists because the
company asked for a two-call contract with no learner, no attempt and no timing in it. Keeping the
two apart is deliberate: if this quietly wrote UC-04 results, an anonymous caller could mint scored
attempts for learners who never sat anything.

TWO DECISIONS WORTH NAMING
--------------------------
**The boundary.** The brief said ``50> Pass && 50<Fail``, which does not say what 50 itself is.
Fifty per cent **passes** — the same rule UC-05 applies (``percentage >= pass_mark``), so a learner
cannot pass one part of this system and fail the other on the same score.

**An unanswered question is wrong, not skipped.** There is no attempt state here to distinguish
"ran out of time" from "did not know", so a missing answer scores zero. Marking it as absent would
let a caller improve their percentage by omitting the questions they were unsure of.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.core.time import Clock, SystemClock
from app.modules.quiz_generation.domain.generation import CourseBrief
from app.modules.quiz_generation.integration.catalogue import CourseLookup, NoCatalogue
from app.modules.quiz_generation.integration.llm import QuestionGeneratorLLM
from app.modules.quiz_generation.integration.question_bank import (
    QuestionSink,
    QuestionView,
)
from app.modules.quiz_generation.models import (
    DEFAULT_PASS_MARK,
    GeneratedQuiz,
    GeneratedQuizQuestion,
)
from app.modules.quiz_generation.services.generation_service import (
    QuestionGenerationService,
)


@dataclass(frozen=True, slots=True)
class QuizQuestionView:
    """One question as the contract returns it."""

    sequence: int
    question_id: str
    question: str
    options: dict[str, str]
    #: The correct option label. Present because the company asked for "MCQs with Keys" — see the
    #: note on the API route about who may receive it.
    answer: str
    explanation: str | None = None


@dataclass(frozen=True, slots=True)
class QuizView:
    quiz_id: str
    topic: str
    course_ref: str | None
    pass_mark: float
    requested_count: int
    questions: tuple[QuizQuestionView, ...] = field(default_factory=tuple)
    #: Questions the model returned that were refused, and why. Reported rather than hidden: a
    #: caller who asked for 20 and got 17 should be able to see that, and why.
    rejected: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MarkedAnswer:
    sequence: int
    question_id: str
    given: str | None
    correct: str
    is_correct: bool


@dataclass(frozen=True, slots=True)
class ResultView:
    quiz_id: str
    total: int
    correct: int
    percentage: float
    pass_mark: float
    passed: bool
    answers: tuple[MarkedAnswer, ...] = field(default_factory=tuple)


class GeneratedQuizService:
    """Create and mark the thin, two-call quizzes."""

    __slots__ = ("_session", "_generation", "_view", "_courses", "_clock")

    def __init__(
        self,
        session: Session,
        *,
        generator: QuestionGeneratorLLM,
        sink: QuestionSink,
        view: QuestionView,
        courses: CourseLookup | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._generation = QuestionGenerationService(generator, sink)
        self._view = view
        # Optional: a caller with no catalogue can still generate from a bare topic.
        self._courses = courses or NoCatalogue()
        self._clock = clock or SystemClock()

    # ---- create -----------------------------------------------------------

    def create(
        self,
        *,
        topic: str,
        count: int,
        course_ref: str | None = None,
        pass_mark: float = DEFAULT_PASS_MARK,
        actor: str | None = None,
    ) -> QuizView:
        """Generate a quiz and remember which questions it asked."""
        subject = " ".join((topic or "").split())
        if not subject:
            raise ValidationError("A topic is required to generate a quiz.")
        if not 0 <= pass_mark <= 100:
            raise ValidationError("The pass mark must be between 0 and 100.")

        # A named course gives the model a description, a level and a module list to work from. An
        # unknown code is not an error — the topic the caller typed still stands on its own.
        brief = (self._courses.find(course_ref) if course_ref else None) or CourseBrief(
            course_id=course_ref or subject, name=subject
        )
        outcome = self._generation.generate(
            brief, count=count, actor=actor, topics=(subject,)
        )

        quiz = GeneratedQuiz(
            topic=subject[:255],
            course_ref=course_ref,
            requested_count=outcome.requested,
            question_count=outcome.created,
            pass_mark=float(pass_mark),
            created_by=actor,
            created_at=self._clock.now(),
        )
        self._session.add(quiz)
        self._session.flush()
        for sequence, question_id in enumerate(outcome.question_ids, start=1):
            self._session.add(
                GeneratedQuizQuestion(
                    quiz_id=quiz.id, question_id=question_id, sequence=sequence
                )
            )
        self._session.commit()

        return QuizView(
            quiz_id=quiz.id,
            topic=quiz.topic,
            course_ref=quiz.course_ref,
            pass_mark=quiz.pass_mark,
            requested_count=quiz.requested_count,
            questions=self._questions(quiz.id),
            rejected=outcome.rejected,
            reasons=outcome.reasons,
        )

    # ---- read -------------------------------------------------------------

    def find(self, quiz_id: str) -> QuizView:
        quiz = self._quiz(quiz_id)
        return QuizView(
            quiz_id=quiz.id,
            topic=quiz.topic,
            course_ref=quiz.course_ref,
            pass_mark=quiz.pass_mark,
            requested_count=quiz.requested_count,
            questions=self._questions(quiz.id),
        )

    # ---- mark -------------------------------------------------------------

    def mark(self, quiz_id: str, answers: dict[str, str]) -> ResultView:
        """Mark submitted answers against the quiz's own questions.

        ``answers`` is keyed either by sequence — ``"1"``, ``"Q1"`` — or by question id, because a
        caller holding the payload this service returned has both and should not have to guess which
        one is expected.
        """
        quiz = self._quiz(quiz_id)
        questions = self._questions(quiz.id)
        if not questions:
            raise ValidationError("This quiz has no questions and cannot be marked.")

        given = self._normalise(answers)
        marked: list[MarkedAnswer] = []
        for question in questions:
            supplied = given.get(str(question.sequence)) or given.get(question.question_id)
            # A missing answer is wrong, not absent. See the module docstring.
            marked.append(
                MarkedAnswer(
                    sequence=question.sequence,
                    question_id=question.question_id,
                    given=supplied,
                    correct=question.answer,
                    is_correct=supplied == question.answer,
                )
            )

        correct = sum(1 for answer in marked if answer.is_correct)
        percentage = round(100.0 * correct / len(marked), 2)
        return ResultView(
            quiz_id=quiz.id,
            total=len(marked),
            correct=correct,
            percentage=percentage,
            pass_mark=quiz.pass_mark,
            # `>=`, matching UC-05, so the same score cannot pass here and fail there.
            passed=percentage >= quiz.pass_mark,
            answers=tuple(marked),
        )

    # ---- internals --------------------------------------------------------

    @staticmethod
    def _normalise(answers: dict[str, str]) -> dict[str, str]:
        """Answers keyed as given, plus a bare-number key for each ``Q1``-style key."""
        normalised: dict[str, str] = {}
        for key, value in (answers or {}).items():
            label = str(value or "").strip().upper()[:1]
            if not label:
                continue
            name = str(key).strip()
            normalised[name] = label
            if name.upper().startswith("Q") and name[1:].isdigit():
                normalised[name[1:]] = label
        return normalised

    def _quiz(self, quiz_id: str) -> GeneratedQuiz:
        quiz = self._session.get(GeneratedQuiz, quiz_id)
        if quiz is None:
            raise NotFoundError("Quiz", quiz_id, code="GENERATED_QUIZ_NOT_FOUND")
        return quiz

    def _questions(self, quiz_id: str) -> tuple[QuizQuestionView, ...]:
        rows = self._session.scalars(
            select(GeneratedQuizQuestion)
            .where(GeneratedQuizQuestion.quiz_id == quiz_id)
            .order_by(GeneratedQuizQuestion.sequence)
        ).all()
        views: list[QuizQuestionView] = []
        for row in rows:
            detail = self._view.read(row.question_id)
            if detail is None:
                # The question was hard-deleted from the bank. Skipping keeps the quiz markable on
                # what remains rather than failing the whole read.
                continue
            views.append(
                QuizQuestionView(
                    sequence=row.sequence,
                    question_id=row.question_id,
                    question=detail.question_text,
                    options=detail.options,
                    answer=detail.answer_label,
                    explanation=detail.explanation,
                )
            )
        return tuple(views)
