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


class MarkedAnswerModel(CamelModel):
    """How one answer was marked.

    **No `correct` field.** Reporting the right answer here would turn the results endpoint into a
    way to read the whole answer key: submit rubbish, read the keys, submit again. `isCorrect` is
    what a marking response needs, and the key stays where it belongs — in the database, and in the
    administrator-only responses.
    """

    sequence: int
    question_id: str
    given: str | None = None
    is_correct: bool


class ResultModel(CamelModel):
    """"Response {Pass / Fail}" — with the arithmetic behind it, so a verdict is checkable."""

    quiz_id: str
    total: int
    correct: int
    percentage: float
    pass_mark: float
    passed: bool
    outcome: str = Field(description="`PASS` or `FAIL`.")
    answers: list[MarkedAnswerModel] = Field(default_factory=list)
