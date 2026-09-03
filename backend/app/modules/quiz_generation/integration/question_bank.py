"""The question bank boundary: where a generated question becomes a stored one.

The only file in this module that imports UC-02, which is what the architecture rule requires and
what makes the dependency reviewable: everything generation knows about the question bank is the
``QuestionSink`` protocol below, and everything the question bank knows about generation is nothing.

WHY THE ADAPTER GOES THROUGH ``create_question`` AND NOT THROUGH SQL
--------------------------------------------------------------------
Inserting rows directly would be quicker and would hold generated questions to a lower standard than
typed ones: no four-option rule, no one-correct-answer rule, no version-1 snapshot. The bank would
then contain two classes of question and only one of them would be trustworthy.

So a generated question takes the same path an administrator's does, and a question the validator
refuses is **dropped and reported** rather than repaired. Repairing it is how a plausible wrong
answer reaches somebody's certificate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.modules.question_bank.domain.drafts import OptionDraft, QuestionDraft
from app.modules.question_bank.domain.enums import QuestionStatus, QuestionType
from app.modules.question_bank.models import Question
from app.modules.question_bank.services.question_service import create_question
from app.modules.quiz_generation.domain.generation import CourseBrief, GeneratedQuestion


@runtime_checkable
class QuestionSink(Protocol):
    """Where accepted questions are written.

    Returns the stored id, or ``None`` and the reason it was refused. A refusal is a normal outcome
    — one bad question out of twenty should cost that question and not the whole run — so it is a
    return value rather than an exception.
    """

    def store(
        self,
        question: GeneratedQuestion,
        brief: CourseBrief,
        *,
        actor: str | None = None,
        topics: tuple[str, ...] = (),
    ) -> tuple[str | None, str | None]: ...


class QuestionBankSink:
    """``QuestionSink`` over UC-02's own creation path."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def store(
        self,
        question: GeneratedQuestion,
        brief: CourseBrief,
        *,
        actor: str | None = None,
        topics: tuple[str, ...] = (),
    ) -> tuple[str | None, str | None]:
        draft = QuestionDraft(
            type=QuestionType.SINGLE_CHOICE,
            # Never ACTIVE. UC-02 delivers only ACTIVE questions, so a DRAFT cannot reach a
            # learner before an administrator has read it.
            status=QuestionStatus.DRAFT,
            question_text=question.question_text,
            explanation=question.explanation,
            topics=list(topics) or ([brief.topic] if brief.topic else []),
            options=[
                OptionDraft(
                    label=option.label,
                    text=option.text,
                    position=index,
                    is_correct=option.is_correct,
                )
                for index, option in enumerate(question.options, start=1)
            ],
        )
        try:
            created = create_question(self._session, draft, actor=actor)
        except Exception as error:  # noqa: BLE001 - the refusal types are UC-02's, not ours
            self._session.rollback()
            return None, f"{type(error).__name__}: {str(error)[:120]}"
        return created.id, None


@runtime_checkable
class QuestionView(Protocol):
    """How a stored question is read back for delivery and for marking.

    Separate from :class:`QuestionSink` because the two have different readers: the sink is used
    once at generation, the view on every delivery and every marking. Keeping them apart means a
    test can fake the expensive half without faking the other.
    """

    def read(self, question_id: str) -> StoredQuestion | None: ...


@dataclass(frozen=True, slots=True)
class StoredQuestion:
    """One question as the thin contract needs it: the stem, the options, and the key.

    The key is here because marking cannot happen without it, and because the company asked for
    "MCQs with Keys". Where it may then travel is decided at the API boundary, not here — see
    ``api/quizzes.py``.
    """

    question_id: str
    question_text: str
    options: dict[str, str]
    answer_label: str
    explanation: str | None = None


class QuestionBankView:
    """``QuestionView`` over UC-02's own tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def read(self, question_id: str) -> StoredQuestion | None:
        question = self._session.get(Question, question_id)
        if question is None:
            return None
        options = sorted(question.options, key=lambda option: option.position)
        correct = [option.label for option in options if option.is_correct]
        if len(correct) != 1:
            # A question with no single correct option cannot be marked. UC-02's validator forbids
            # this on creation, so reaching it means the row was edited outside that path — better
            # to treat the question as absent than to mark somebody against it.
            return None
        return StoredQuestion(
            question_id=question.id,
            question_text=question.question_text,
            options={option.label: option.text for option in options},
            answer_label=correct[0],
            explanation=question.explanation,
        )
