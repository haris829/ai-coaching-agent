"""Generate a quiz from a course, then mark it.

The contract the company sketched, in four routes::

    POST /v1/generated-quizzes                        admin      -> quizId, MCQs + keys, passMark
    GET  /v1/generated-quizzes/{quiz_id}              any caller -> the quiz to sit, without keys
    POST /v1/generated-quizzes/{quiz_id}/results      any caller -> PASS / FAIL
    GET  /v1/generated-quizzes/{quiz_id}/answers      admin      -> the keys
    GET  /v1/generated-quizzes/{quiz_id}/submissions  admin      -> every stored sitting, in detail

NOT UNDER ``/v1/quizzes``
-------------------------
UC-01's quizzes are identified by an **integer**; a generated quiz is identified by a **UUID**. Put
them in one namespace and ``/v1/quizzes/{quiz_id}/results`` sits next to
``/v1/quizzes/{quiz_id}/retakes`` with two incompatible notions of ``quiz_id`` — an integrator who
passes the wrong one gets a 404 from one route and a 422 from the other, and neither says why. The
separate prefix makes which kind of quiz is being addressed unambiguous from the URL.

WHERE THE ANSWER KEY IS ALLOWED TO GO
-------------------------------------
The brief asked for "MCQs with Keys", and generation returns them — to an **administrator**. The two
routes a person sitting a quiz uses carry no key at all: the delivery route omits it, and the
marking route reports only whether each answer was right.

That is not over-caution. Without it, the marking route is an answer-key oracle: submit twenty
guesses, read the corrections, submit again and pass. The key stays in the database and marking
happens against it server-side, which is the only arrangement where a pass means anything.

**The learner is not told their own answers either.** The marking route returns the verdict and the
score, and says nothing about which questions were right — that is the company's own contract,
``Response {Pass / Fail}``. Every answer *is* recorded and marked in ``qz_submitted_answers``, and
an administrator reads it back through the submissions route. Stored, not returned.

WHY GENERATION IS ADMIN-ONLY
----------------------------
It spends money on a model call and it writes rows into the question bank. Both are administrative
acts. An unauthenticated generate endpoint is a bill and a content-injection path at the same time.

WHAT A GENERATED QUESTION IS NOT
--------------------------------
Every question lands in UC-02 as **DRAFT**, so nothing generated here can be delivered by UC-03's
real attempt flow until an administrator activates it. These four routes are the thin, self-marking
contract; the full flow — configuration versions, timed attempts, immutable results, certification —
is UC-01 through UC-06 and is untouched by anything here.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.modules.identity.security import AdminPrincipal, CurrentPrincipal
from app.modules.quiz_generation.api.dependencies import QuizServiceDep
from app.modules.quiz_generation.api.schemas import (
    GeneratedQuizModel,
    GenerateQuizRequest,
    KeyedQuestionModel,
    MarkedAnswerModel,
    OptionModel,
    QuestionModel,
    QuizModel,
    ResultModel,
    StoredSubmissionModel,
    SubmissionListModel,
    SubmitAnswersRequest,
)
from app.modules.quiz_generation.services.quiz_service import (
    QuizQuestionView,
    QuizView,
    ResultView,
)

router = APIRouter(prefix="/generated-quizzes", tags=["Quiz Generation"])


def _options(question: QuizQuestionView) -> list[OptionModel]:
    return [OptionModel(label=label, text=text) for label, text in question.options.items()]


def _unkeyed(question: QuizQuestionView) -> QuestionModel:
    return QuestionModel(
        sequence=question.sequence,
        question_id=question.question_id,
        question=question.question,
        options=_options(question),
    )


def _keyed(question: QuizQuestionView) -> KeyedQuestionModel:
    return KeyedQuestionModel(
        sequence=question.sequence,
        question_id=question.question_id,
        question=question.question,
        options=_options(question),
        answer=question.answer,
        explanation=question.explanation,
    )


def _verdict(result: ResultView) -> ResultModel:
    """The verdict and the score. No per-answer detail — that is stored, not returned."""
    return ResultModel(
        submission_id=result.submission_id,
        quiz_id=result.quiz_id,
        total=result.total,
        correct=result.correct,
        percentage=result.percentage,
        pass_mark=result.pass_mark,
        passed=result.passed,
        outcome="PASS" if result.passed else "FAIL",
    )


def _generated(view: QuizView) -> GeneratedQuizModel:
    return GeneratedQuizModel(
        quiz_id=view.quiz_id,
        topic=view.topic,
        course_ref=view.course_ref,
        pass_mark=view.pass_mark,
        requested_count=view.requested_count,
        question_count=len(view.questions),
        questions=[_keyed(question) for question in view.questions],
        rejected=view.rejected,
        reasons=list(view.reasons),
    )


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------

_GENERATE_DESCRIPTION = (
    "Writes the questions into the question bank as **DRAFT** and returns the quiz with its "
    "answer key.\n\n"
    "Fewer questions may come back than were asked for. Anything the validator refuses — three "
    "options instead of four, a correct answer that is not one of the options, a repeat of an "
    "earlier question — is dropped and counted in `rejected`, never repaired. Repairing a "
    "malformed question is how a plausible wrong answer reaches somebody's certificate.\n\n"
    "Pass `courseRef` where you can: a course looked up in the catalogue carries a description "
    "and an RQF level, and questions pitched at the right level are the whole point.\n\n"
    "Returns **503** when no AI provider is configured, and **502** when the provider was reached "
    "but produced nothing usable. Neither writes any questions."
)


@router.post(
    "",
    response_model=GeneratedQuizModel,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a quiz from a course, with its answer key",
    description=_GENERATE_DESCRIPTION,
)
def generate_quiz(
    payload: GenerateQuizRequest,
    service: QuizServiceDep,
    admin: AdminPrincipal,
) -> GeneratedQuizModel:
    view = service.create(
        topic=payload.topic,
        count=payload.count,
        course_ref=payload.course_ref,
        pass_mark=payload.pass_mark,
        actor=str(admin.id),
    )
    # No "empty quiz" branch here. QuestionGenerationService raises before anything is stored when
    # nothing survives parsing, so a 502 is already on its way and no quiz row exists to return.
    return _generated(view)


# ---------------------------------------------------------------------------
# Sit it
# ---------------------------------------------------------------------------


@router.get(
    "/{quiz_id}",
    response_model=QuizModel,
    summary="The quiz as it is sat — questions and options, no answers",
    description=(
        "Everything needed to present the quiz and nothing more. The answer key is deliberately "
        "absent: it lives in the database, and marking happens there."
    ),
)
def read_quiz(quiz_id: str, service: QuizServiceDep, _caller: CurrentPrincipal) -> QuizModel:
    view = service.find(quiz_id)
    return QuizModel(
        quiz_id=view.quiz_id,
        topic=view.topic,
        course_ref=view.course_ref,
        pass_mark=view.pass_mark,
        question_count=len(view.questions),
        questions=[_unkeyed(question) for question in view.questions],
    )


_RESULTS_DESCRIPTION = (
    'Answers are keyed by position ("1" or "Q1") or by `questionId`; each value is one of '
    "A, B, C, D.\n\n"
    "**A question left out is marked wrong**, not skipped. There is no attempt state here to tell "
    "'ran out of time' from 'did not know', so omitting a question cannot improve a percentage.\n\n"
    "The verdict uses the pass mark frozen onto the quiz when it was generated, and **50% passes** "
    "— the same boundary UC-05 applies, so one score cannot pass here and fail there.\n\n"
    "**Returns the verdict and the score only.** Not which answers were right, and not what the "
    "right answers were. Every answer is stored and marked in the database, and an administrator "
    "can read it back from `/submissions` — the learner is simply not told. Per-question "
    "corrections here would also make this route a way to read the whole answer key."
)


@router.post(
    "/{quiz_id}/results",
    response_model=ResultModel,
    summary="Mark submitted answers and return pass or fail",
    description=_RESULTS_DESCRIPTION,
)
def submit_answers(
    quiz_id: str,
    payload: SubmitAnswersRequest,
    service: QuizServiceDep,
    caller: CurrentPrincipal,
) -> ResultModel:
    result: ResultView = service.mark(quiz_id, payload.answers, learner_ref=str(caller.id))
    # `result.answers` carries the per-question detail. It is written to the database and
    # deliberately kept out of this response — see the module docstring.
    return _verdict(result)


# ---------------------------------------------------------------------------
# Read the key back
# ---------------------------------------------------------------------------


@router.get(
    "/{quiz_id}/answers",
    response_model=GeneratedQuizModel,
    summary="The quiz with its answer key (administrators only)",
    description=(
        "The same body `POST /generated-quizzes` returned, for an integration that needs the key "
        "again without paying for a second generation."
    ),
)
def read_answers(
    quiz_id: str, service: QuizServiceDep, _admin: AdminPrincipal
) -> GeneratedQuizModel:
    return _generated(service.find(quiz_id))


_SUBMISSIONS_DESCRIPTION = (
    "What was answered, what was correct, and the verdict — for each sitting, newest first.\n\n"
    "This is the detail the marking route deliberately withholds from the learner. It is recorded "
    "in `qz_quiz_submissions` and `qz_submitted_answers` at the moment of marking, so \"did this "
    "person pass, and on what\" is answerable from the database rather than from whoever still "
    "holds the response.\n\n"
    "The correct answer shown here is read from the question bank, not from a copy kept alongside "
    "the submission — a second copy could disagree with the first, and the one that disagreed "
    "would be the one somebody quoted."
)


@router.get(
    "/{quiz_id}/submissions",
    response_model=SubmissionListModel,
    summary="Every stored sitting of this quiz, in detail (administrators only)",
    description=_SUBMISSIONS_DESCRIPTION,
)
def read_submissions(
    quiz_id: str, service: QuizServiceDep, _admin: AdminPrincipal
) -> SubmissionListModel:
    return SubmissionListModel(
        quiz_id=quiz_id,
        submissions=[
            StoredSubmissionModel(
                **_verdict(result).model_dump(),
                answers=[
                    MarkedAnswerModel(
                        sequence=answer.sequence,
                        question_id=answer.question_id,
                        given=answer.given,
                        correct=answer.correct,
                        is_correct=answer.is_correct,
                    )
                    for answer in result.answers
                ],
            )
            for result in service.submissions(quiz_id)
        ],
    )
