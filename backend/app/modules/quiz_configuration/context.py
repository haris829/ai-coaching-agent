"""The collaborators a UC-01 operation needs, assembled once per request.

Services take a :class:`QuizConfigurationContext` rather than a ``Session``. That is what keeps
the business rules honest about their dependencies: a service can only reach the repositories and
the question-bank port it was handed, so it cannot quietly grow a direct query. Swapping in the
company's repositories, or a fake question bank in a unit test, is a matter of constructing a
different context.

The context also carries the transaction: :meth:`commit` and :meth:`rollback` are the only places
a UC-01 service touches the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import DbSession
from app.modules.quiz_configuration.integration.attempt_statistics_adapter import (
    AttemptStatisticsAdapter,
)
from app.modules.quiz_configuration.integration.question_bank_adapter import QuestionBankAdapter
from app.modules.quiz_configuration.ports import AttemptStatisticsPort, QuestionBankPort
from app.modules.quiz_configuration.repositories import (
    ConfigurationVersionRepository,
    QuizRepository,
    SqlAlchemyConfigurationVersionRepository,
    SqlAlchemyQuizRepository,
)


@dataclass(frozen=True, slots=True)
class QuizConfigurationContext:
    db: Session
    quizzes: QuizRepository
    versions: ConfigurationVersionRepository
    bank: QuestionBankPort
    #: Read-only view of UC-03's attempts. UC-01 counts them; it never creates one.
    attempt_stats: AttemptStatisticsPort

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def refresh(self, instance: object) -> None:
        self.db.refresh(instance)


def build_context(
    db: Session,
    *,
    bank: QuestionBankPort | None = None,
    attempt_stats: AttemptStatisticsPort | None = None,
) -> QuizConfigurationContext:
    """Assemble the local implementations. The ports are injectable for tests."""
    return QuizConfigurationContext(
        db=db,
        quizzes=SqlAlchemyQuizRepository(db),
        versions=SqlAlchemyConfigurationVersionRepository(db),
        bank=bank or QuestionBankAdapter(db),
        attempt_stats=attempt_stats or AttemptStatisticsAdapter(db),
    )


def get_context(db: DbSession) -> QuizConfigurationContext:
    return build_context(db)


ContextDep = Annotated[QuizConfigurationContext, Depends(get_context)]
