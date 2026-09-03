"""Request-scoped assembly of the generation service.

The service needs four collaborators, and they have different lifetimes. The model client is built
from settings and is safe to share; the question sink, the question view and the catalogue lookup
all read and write through **this request's** session, so they are built per request. Sharing a
session across requests is how one caller's uncommitted work becomes another's.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from app.core.config import get_settings
from app.core.deps import DbSession
from app.modules.quiz_generation.integration.catalogue import CatalogueLookup
from app.modules.quiz_generation.integration.llm import build_generator
from app.modules.quiz_generation.integration.question_bank import (
    GeneratedHistory,
    QuestionBankSink,
    QuestionBankView,
)
from app.modules.quiz_generation.services.quiz_service import GeneratedQuizService


def get_quiz_service(db: DbSession) -> Iterator[GeneratedQuizService]:
    settings = get_settings()
    yield GeneratedQuizService(
        db,
        generator=build_generator(settings),
        sink=QuestionBankSink(db),
        view=QuestionBankView(db),
        courses=CatalogueLookup(db),
        # Without this, every generation starts from a blank slate and can hand back a paper the
        # course has already been given.
        history=GeneratedHistory(db),
    )


QuizServiceDep = Annotated[GeneratedQuizService, Depends(get_quiz_service)]
