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

MARKING READS THE QUIZ, NOT THE QUESTION BANK
---------------------------------------------
The stem, the options and the answer key are frozen onto the quiz when it is generated, and marking
uses that. It is worth saying why, because this file previously did the opposite and the opposite
was wrong.

Reading the key live from UC-02 means one copy of a question and no chance of two copies
disagreeing — which sounds right until a question is edited. Then every quiz that used it is
silently rewritten, and every sitting ever marked against it now reports a "correct" answer that
was not correct when the learner sat it. A stored result has to mean what it meant at the time.
UC-03 freezes the questions it delivered and UC-04 makes a confirmed result immutable for this
exact reason; a generated quiz gets the same treatment.

The question bank is still where a question is reviewed, edited and retired. It is simply not the
authority on what *this* quiz asked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.core.time import Clock, SystemClock
from app.modules.quiz_generation.domain.generation import CourseBrief
from app.modules.quiz_generation.integration.catalogue import (
    CourseLookup,
    CourseSummary,
    NoCatalogue,
)
from app.modules.quiz_generation.integration.llm import QuestionGeneratorLLM
from app.modules.quiz_generation.integration.question_bank import (
    QuestionHistory,
    QuestionSink,
    QuestionView,
)
from app.modules.quiz_generation.models import (
    DEFAULT_PASS_MARK,
    GeneratedQuiz,
    GeneratedQuizQuestion,
    QuizSubmission,
    SubmittedAnswer,
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
    #: The stored sitting this verdict came from. The verdict is a row, not just a response.
    submission_id: str
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
        history: QuestionHistory | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._generation = QuestionGenerationService(generator, sink, history)
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
        # unknown reference is not an error — the topic the caller typed still stands on its own.
        #
        # The topic is tried as a course reference too, because a caller who sends only
        # {"topic": "Medical Law MA"} has named a course whether or not they used the courseRef
        # field. Looking it up costs one indexed query and is the difference between generating
        # from a syllabus and generating from four words.
        matched = (self._courses.find(course_ref) if course_ref else None) or self._courses.find(
            subject
        )
        brief = matched or CourseBrief(course_id=course_ref or subject, name=subject)
        # Record WHICH course was matched, even when the caller sent only a name. Without this the
        # response says `courseRef: null` for a request that did resolve, and a caller has no way to
        # tell a match from a silent miss — and a silent miss is exactly the case worth seeing,
        # because it means the quiz was generated from a bare string rather than from a syllabus.
        resolved_ref = matched.course_id if matched else course_ref
        outcome = self._generation.generate(
            brief, count=count, actor=actor, topics=(subject,)
        )

        quiz = GeneratedQuiz(
            topic=subject[:255],
            course_ref=resolved_ref,
            requested_count=outcome.requested,
            question_count=outcome.created,
            pass_mark=float(pass_mark),
            created_by=actor,
            created_at=self._clock.now(),
        )
        self._session.add(quiz)
        self._session.flush()
        for sequence, question_id in enumerate(outcome.question_ids, start=1):
            # Read the question back from the bank and freeze it onto the quiz. Read rather than
            # taken from the generation result on purpose: this is the question as UC-02's
            # validator actually stored it, which is what a learner would have been shown.
            detail = self._view.read(question_id)
            self._session.add(
                GeneratedQuizQuestion(
                    quiz_id=quiz.id,
                    question_id=question_id,
                    sequence=sequence,
                    question_text=detail.question_text if detail else None,
                    options_json=json.dumps(detail.options) if detail else None,
                    answer_label=detail.answer_label if detail else None,
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

    def courses(self, limit: int = 200) -> tuple[CourseSummary, ...]:
        """The courses available to generate from.

        Exists because choosing a course by typing its code means knowing the code. A person
        generating a quiz picks from a list of names.
        """
        return self._courses.list_all(limit)

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

    def mark(
        self, quiz_id: str, answers: dict[str, str], *, learner_ref: str | None = None
    ) -> ResultView:
        """Mark submitted answers against the quiz's own questions, and store the sitting.

        ``answers`` is keyed either by sequence — ``"1"``, ``"Q1"`` — or by question id, because a
        caller holding the payload this service returned has both and should not have to guess which
        one is expected.

        The verdict is **written before it is returned**. A pass that exists only in an HTTP
        response is not a record of anything, and "did this person pass, and on what" has to be
        answerable from the database later.
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
        # `>=`, matching UC-05, so the same score cannot pass here and fail there.
        passed = percentage >= quiz.pass_mark

        submission = QuizSubmission(
            quiz_id=quiz.id,
            learner_ref=learner_ref,
            total=len(marked),
            correct=correct,
            percentage=percentage,
            # Copied, not referenced — a stored row should be readable without a second lookup.
            pass_mark=quiz.pass_mark,
            passed=passed,
            submitted_at=self._clock.now(),
        )
        self._session.add(submission)
        self._session.flush()
        for answer in marked:
            self._session.add(
                SubmittedAnswer(
                    submission_id=submission.id,
                    question_id=answer.question_id,
                    sequence=answer.sequence,
                    # None for a question left unanswered. Recording the absence means the stored
                    # sitting accounts for every question that was asked.
                    given_label=answer.given,
                    is_correct=answer.is_correct,
                )
            )
        self._session.commit()

        return ResultView(
            submission_id=submission.id,
            quiz_id=quiz.id,
            total=len(marked),
            correct=correct,
            percentage=percentage,
            pass_mark=quiz.pass_mark,
            passed=passed,
            answers=tuple(marked),
        )

    def submissions(self, quiz_id: str) -> tuple[ResultView, ...]:
        """Every stored sitting of a quiz, newest first. For an administrator to read back."""
        quiz = self._quiz(quiz_id)
        rows = self._session.scalars(
            select(QuizSubmission)
            .where(QuizSubmission.quiz_id == quiz.id)
            .order_by(QuizSubmission.submitted_at.desc())
        ).all()
        # One lookup of the quiz's own keys, shared by every sitting, rather than a bank read per
        # answer per sitting.
        keys = {
            question.sequence: question.answer for question in self._questions(quiz.id)
        }
        return tuple(
            ResultView(
                submission_id=row.id,
                quiz_id=row.quiz_id,
                total=row.total,
                correct=row.correct,
                percentage=row.percentage,
                pass_mark=row.pass_mark,
                passed=row.passed,
                answers=tuple(
                    MarkedAnswer(
                        sequence=answer.sequence,
                        question_id=answer.question_id,
                        given=answer.given_label,
                        # From the quiz's frozen snapshot, so a stored sitting reports the answer
                        # that was correct when it was sat.
                        correct=keys.get(answer.sequence, "?"),
                        is_correct=answer.is_correct,
                    )
                    for answer in row.answers
                ),
            )
            for row in rows
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
        """The quiz's questions as it asked them.

        From the frozen snapshot on each row, so a later edit to the bank cannot change what this
        quiz was. Rows written before the snapshot existed have none, and those fall back to the
        bank — the only case where a bank edit still shows through, and it applies to a handful of
        rows from before this changed rather than to anything generated since.
        """
        rows = self._session.scalars(
            select(GeneratedQuizQuestion)
            .where(GeneratedQuizQuestion.quiz_id == quiz_id)
            .order_by(GeneratedQuizQuestion.sequence)
        ).all()
        views: list[QuizQuestionView] = []
        for row in rows:
            view = self._frozen(row) or self._from_bank(row)
            if view is None:
                # No snapshot and the question is gone from the bank. Skipping keeps the quiz
                # markable on what remains rather than failing the whole read.
                continue
            views.append(view)
        return tuple(views)

    @staticmethod
    def _frozen(row: GeneratedQuizQuestion) -> QuizQuestionView | None:
        """The question as the quiz froze it, or ``None`` if this row predates the snapshot."""
        if not row.question_text or not row.answer_label or not row.options_json:
            return None
        try:
            options = json.loads(row.options_json)
        except ValueError:  # pragma: no cover - would mean a corrupted row
            return None
        if not isinstance(options, dict) or not options:
            return None
        return QuizQuestionView(
            sequence=row.sequence,
            question_id=row.question_id,
            question=row.question_text,
            options={str(k): str(v) for k, v in options.items()},
            answer=row.answer_label,
            # Not frozen: an explanation is read after the fact and is the bank's current best
            # wording, not part of what was asked. Nothing is marked against it.
            explanation=None,
        )

    def _from_bank(self, row: GeneratedQuizQuestion) -> QuizQuestionView | None:
        detail = self._view.read(row.question_id)
        if detail is None:
            return None
        return QuizQuestionView(
            sequence=row.sequence,
            question_id=row.question_id,
            question=detail.question_text,
            options=detail.options,
            answer=detail.answer_label,
            explanation=detail.explanation,
        )
