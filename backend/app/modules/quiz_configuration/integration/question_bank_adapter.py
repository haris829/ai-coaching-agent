"""The question bank, seen through UC-01's port.

This is the only file in UC-01 that knows the question bank exists. It answers three questions:

* how many questions of each type can a future quiz actually use? (capacity validation)
* which questions should this attempt receive? (start quiz)
* what did this attempt receive? (attempt detail)

All three go through ``delivery_service``, whose queries filter on deliverable status. That is
deliberate: it means "retired questions do not count and are never delivered" is enforced by the
bank's own query builder, not restated — and therefore not forgettable — here.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.question_bank.models import Topic
from app.modules.question_bank.services import delivery_service
from app.modules.quiz_configuration.domain.enums import QuestionType
from app.modules.quiz_configuration.ports import (
    BankScope,
    TopicRef,
)


class QuestionBankAdapter:
    """:class:`~app.modules.quiz_configuration.ports.QuestionBankPort` over the local bank."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # -- capacity ---------------------------------------------------------

    def available_by_type(self, scope: BankScope) -> dict[QuestionType, int]:
        requested = scope.types or tuple(QuestionType)
        counts = delivery_service.count_deliverable_by_type(
            self._db,
            types=[item.value for item in requested],
            topic_ids=list(scope.topic_ids) or None,
        )
        # Types with nothing eligible must report 0 rather than be absent, otherwise a shortfall
        # would look like "no constraint" to the capacity rule.
        return {item: int(counts.get(item.value, 0)) for item in requested}

    # -- topic scope ------------------------------------------------------

    def resolve_topics(self, topic_ids: Sequence[str]) -> list[TopicRef]:
        if not topic_ids:
            return []
        rows = self._db.execute(select(Topic).where(Topic.id.in_(list(topic_ids)))).scalars().all()
        found = {topic.id: topic for topic in rows}
        # Preserve the caller's order so a frozen scope reads the way it was entered.
        return [
            TopicRef(id=topic.id, slug=topic.slug, name=topic.name)
            for topic in (found.get(topic_id) for topic_id in topic_ids)
            if topic is not None
        ]

    # -- drawing questions ------------------------------------------------

