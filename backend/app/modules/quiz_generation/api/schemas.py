"""Request and response shapes for the generate-and-mark contract.

Field names are camelCase on the wire, like every other API in this application — the
``CamelModel`` base in ``app.core.schemas`` handles it, so a Python ``pass_mark`` arrives and
leaves as ``passMark``.
"""

from __future__ import annotations

from pydantic import Field

from app.core.schemas import CamelModel
from app.modules.quiz_generation.domain.generation import MAX_QUESTIONS_PER_REQUEST
from app.modules.quiz_generation.models import DEFAULT_PASS_MARK


class GenerateQuizRequest(CamelModel):
    """"Payload {Set of question, Topic}" from the company's sketch, plus what sharpens it."""

    topic: str = Field(
        min_length=2,
        max_length=255,
        description="What the questions should be about. A course title works.",
    )
    count: int = Field(
        default=20,
        ge=1,
        le=MAX_QUESTIONS_PER_REQUEST,
        description="How many questions to ask for. Fewer may come back — see `rejected`.",
    )
    course_ref: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "A course code from the catalogue, e.g. `LL-37533`. When given, the course's own "
            "description, level and modules are read from the database and used to aim the "
            "questions — which produces markedly better ones than a bare topic."
        ),
    )
    pass_mark: float = Field(
        default=DEFAULT_PASS_MARK,
        ge=0,
        le=100,
        description=(
            "The percentage needed to pass, frozen onto this quiz. Defaults to 50, the threshold "
            "in the brief."
        ),
    )


class OptionModel(CamelModel):
    label: str
    text: str


class QuestionModel(CamelModel):
    """One question as a client sits it. No key — see `KeyedQuestionModel`."""

    sequence: int
    question_id: str
    question: str
    options: list[OptionModel]


class KeyedQuestionModel(QuestionModel):
    """One question with its answer key, returned only to an administrator."""

    answer: str = Field(description="The correct option's label.")
    explanation: str | None = None


class QuizModel(CamelModel):
    quiz_id: str
    topic: str
    course_ref: str | None = None
    pass_mark: float
    question_count: int
    questions: list[QuestionModel]


class GeneratedQuizModel(CamelModel):
    """The generate response: the quiz, its keys, and an honest account of the yield."""

    quiz_id: str
    topic: str
    course_ref: str | None = None
    pass_mark: float
    requested_count: int
    question_count: int
    questions: list[KeyedQuestionModel]
    rejected: int = Field(
        default=0,
        description=(
            "Questions the model produced that were refused by validation, and therefore not "
            "stored. Reported rather than hidden: a request for 20 that yields 17 should say so."
        ),
    )
    reasons: list[str] = Field(default_factory=list)


class SubmitAnswersRequest(CamelModel):
    """"Post {Q-ID, Q1-Q20 : Answers(A,B,C & D)}" from the sketch."""

    answers: dict[str, str] = Field(
        description=(
            "Answers keyed by question position (`\"1\"` or `\"Q1\"`) or by `questionId`, each "
            "value one of A, B, C, D. A question left out is marked wrong."
        ),
        examples=[{"Q1": "B", "Q2": "A", "Q3": "D"}],
    )


class ResultModel(CamelModel):
    """"Response {Pass / Fail}" — the verdict and the arithmetic behind it, and nothing else.

    **Deliberately says nothing about individual answers.** Not which ones were right, and not what
    the right ones were. Two reasons, and they are different:

    * Per-question corrections would make this route an answer-key oracle — submit twenty guesses,
      read which were wrong, submit again and pass.
    * The learner is not told their answers at all. That is the company's own contract:
      ``Response {Pass / Fail}``.

    The full detail *is* recorded — every answer, marked, in ``qz_submitted_answers`` — and an
    administrator can read it through ``GET /generated-quizzes/{id}/submissions``. It is stored and
    not returned, which is the distinction that matters.

    ``correct`` is a count, not a list, so it gives the percentage its meaning without saying which
    questions it refers to.
    """

    submission_id: str = Field(
        description="The stored sitting. The verdict is a row, not only this response."
    )
    quiz_id: str
    total: int
    correct: int = Field(description="How many were right. A count only — never which ones.")
    percentage: float
    pass_mark: float
    passed: bool
    outcome: str = Field(description="`PASS` or `FAIL`.")


class MarkedAnswerModel(CamelModel):
    """One answer of a stored sitting, as an **administrator** reads it back.

    This is the only shape that pairs a learner's answer with the correct one, and it is returned
    only from the administrator-only submissions route.
    """

    sequence: int
    question_id: str
    given: str | None = Field(default=None, description="`None` if the question was not answered.")
    correct: str
    is_correct: bool


class StoredSubmissionModel(ResultModel):
    """A stored sitting with its per-answer detail. Administrators only."""

    answers: list[MarkedAnswerModel] = Field(default_factory=list)


class SubmissionListModel(CamelModel):
    quiz_id: str
    submissions: list[StoredSubmissionModel] = Field(default_factory=list)


class CourseSummaryModel(CamelModel):
    """One course to choose from when generating."""

    code: str
    title: str
    rqf_level: int | None = None
    subject_area: str | None = None
    has_brief: bool = Field(
        default=False,
        description=(
            "Whether this course has a description to generate from. A course without one is "
            "generated from its title alone, which produces noticeably more generic questions."
        ),
    )
    generated_count: int = Field(
        default=0, description="How many quizzes have already been generated for this course."
    )


class CourseListModel(CamelModel):
    courses: list[CourseSummaryModel] = Field(default_factory=list)
