"""UC-02, behind UC-08's ``QuestionBankProvider``.

Like the UC-01 adapter, this delegates to the reader UC-03 already uses rather than querying
``qb_`` rows itself — so the pool UC-08 sizes is, by construction, the same eligible pool UC-03
will select from moments later. A retake planned against a pool that differed from the delivery
pool would report reuse that did not happen, or miss reuse that did.

**Content stops here.** UC-03's ``BankQuestion`` carries prompts, options and answer keys;
:class:`~app.modules.retakes.integration.uc02.QuestionDescriptor` carries an id, a type and a
topic. The narrowing happens in this file, at the boundary, which is why nothing downstream in
UC-08 is capable of holding an answer key even by accident.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.modules.attempt_delivery.integration.uc02.question_bank_adapter import (
    Uc02QuestionBankAdapter,
)
from app.modules.attempt_delivery.integration.uc02.types import QuestionQuery
from app.modules.retakes.domain.errors import QuestionBankUnavailableError
from app.modules.retakes.integration.uc02 import QuestionDescriptor, QuestionPoolQuery


class RetakeQuestionBankAdapter:
    """``QuestionBankProvider`` over UC-02, through UC-03's reader."""

    __slots__ = ("_delegate",)

    def __init__(self, session: Session) -> None:
        self._delegate = Uc02QuestionBankAdapter(session)

    async def find_eligible_questions(
        self, query: QuestionPoolQuery
    ) -> tuple[QuestionDescriptor, ...]:
        return await offload(self._find_eligible_questions, query)

    def _find_eligible_questions(
        self, query: QuestionPoolQuery
    ) -> tuple[QuestionDescriptor, ...]:
        try:
            questions = self._delegate.find_eligible_questions(
                QuestionQuery(
                    quiz_id=query.quiz_id,
                    course_id=query.course_id,
                    topic_ids=tuple(query.topic_ids),
                    # UC-08 never sets this False, and the delegate is the same call UC-03 makes,
                    # so a retired question is absent from the pool rather than filtered out of
                    # it later. Reuse is preferable to reaching for a retired question (§8).
                    exclude_retired=query.exclude_retired,
                )
            )
        except SQLAlchemyError as exc:
            # An unreadable bank must not look like "no alternatives exist" — that would make a
            # retake record unavoidable reuse it never had to accept.
            raise QuestionBankUnavailableError(cause=exc) from exc

        # No shuffle, no limit: sizing the pool is the caller's job, and a truncated pool would
        # silently understate how many alternatives the learner has.
        wanted = {str(item) for item in query.types}
        return tuple(
            QuestionDescriptor(
                question_id=question.question_id,
                question_type=str(question.type),
                topic_id=question.topic_id,
                retired=False,
            )
            for question in questions
            if not wanted or str(question.type) in wanted
        )
