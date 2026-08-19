"""Seed the question bank with a known shape.

UC-01's capacity rule is only meaningful against a real bank, so the configuration tests stock the
actual ``qb_*`` tables through the actual service — no fixtures poking rows in directly. That is
the point: a test that says "10 single-choice questions are available" has proved the whole path
from question creation to capacity arithmetic.
"""

from __future__ import annotations

from itertools import count as _sequence
from typing import Any

from sqlalchemy.orm import Session

from app.modules.question_bank.domain.enums import QuestionType
from app.modules.question_bank.models import Question, Topic
from app.modules.question_bank.services import question_service, topic_service
from tests import factories

#: A bank shape that satisfies the default test configuration.
DEFAULT_BANK: dict[QuestionType, int] = {
    QuestionType.SINGLE_CHOICE: 15,
    QuestionType.TRUE_FALSE: 15,
    QuestionType.MULTI_SELECT: 5,
    QuestionType.SCENARIO: 5,
    QuestionType.DRAG_TO_ORDER: 5,
}


#: Process-wide counter, so two seeding calls in the same test never produce the same question.
#: The bank rejects duplicate content by design, and a seeded bank of N must really be N distinct
#: questions for a capacity assertion to mean anything.
_counter = _sequence(1)


def _payload(question_type: QuestionType, topics: list[str] | None) -> dict[str, Any]:
    """A valid, unique payload for one question."""
    builder = factories.ALL_BUILDERS[question_type.value]
    payload = builder()
    payload["questionText"] = f"{payload['questionText']} (bank item #{next(_counter)})"
    if topics is not None:
        payload["topics"] = list(topics)
    return payload


def seed_questions(
    db: Session,
    question_type: QuestionType,
    count: int,
    *,
    topics: list[str] | None = None,
    status: str = "ACTIVE",
) -> list[Question]:
    """Create ``count`` questions of one type, committed."""
    created: list[Question] = []
    for _ in range(count):
        payload = _payload(question_type, topics)
        payload["status"] = status
        draft = question_service.draft_from_payload(payload)
        created.append(
            question_service.create_question(db, draft, actor="seed", commit=False)
        )
    db.commit()
    for question in created:
        db.refresh(question)
    return created


def seed_bank(
    db: Session,
    plan: dict[QuestionType, int] | None = None,
    *,
    topics: list[str] | None = None,
) -> dict[QuestionType, list[Question]]:
    """Stock the bank per ``plan``. Defaults to :data:`DEFAULT_BANK`."""
    result: dict[QuestionType, list[Question]] = {}
    for question_type, count in (DEFAULT_BANK if plan is None else plan).items():
        if count:
            result[question_type] = seed_questions(db, question_type, count, topics=topics)
        else:
            result[question_type] = []
    return result


def topic_named(db: Session, name: str) -> Topic:
    """Fetch or create a topic, so a test can scope a configuration to it."""
    topics = topic_service.resolve_topics(db, names=[name], ids=None, auto_create=True)
    db.commit()
    return topics[0]


def retire_all(db: Session, questions: list[Question], reason: str = "test") -> None:
    for question in questions:
        question_service.retire_question(db, question.id, reason=reason, actor="seed")
