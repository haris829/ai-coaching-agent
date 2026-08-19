"""UC-02's question bank, seen through UC-03's port.

Replaces the provisional ``LocalQuestionBankAdapter`` that read opaque JSON out of
``ext_questions``. Almost entirely read-only: UC-03 selects and snapshots, and never authors. The
one write is :meth:`Uc02QuestionBankAdapter.record_delivery`, which tells the bank that a question
was
delivered — see the port for why that record belongs to UC-02 rather than being a duplicate
of UC-03's
own snapshot.

The eligible-question query is built from the bank's own
:func:`~app.modules.question_bank.services.delivery_service.deliverable_conditions`, so *"a retired
question is never delivered to a new attempt"* remains one rule enforced by one query builder —
shared with UC-01's capacity counting. A hand-rolled status filter here is exactly how a retired
question eventually reaches a learner.

Three shape translations, all confined to this file:

**Options vs order items.** UC-02 stores both as ``qb_question_options`` rows, distinguished by
whether ``correct_position`` is set. UC-03 wants them as separate ``options`` / ``order_items``
collections.

**SCENARIO.** UC-02 models it as a vignette plus one question with a primary answer. UC-03 models it
as a stem plus ``sub_questions``, each behaving as a primitive type. UC-02's shape maps onto exactly
one single-choice sub-question — which is the mapping UC-03's own design notes anticipated.

**Scoping.** UC-02's bank is global and tagged by topic, not partitioned by quiz or course. The
query's ``quiz_id``/``course_id`` are therefore not filters; the topic scope frozen onto the
configuration version is what narrows the pool. Recorded here rather than silently ignored.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.logging import get_logger
from app.core.question_types import QuestionStatus
from app.modules.attempt_delivery.domain.enums import QuestionType
from app.modules.attempt_delivery.integration.uc02.types import (
    BankQuestion,
    DeliveredQuestionRef,
    QuestionOption,
    QuestionOrderItem,
    QuestionQuery,
    ScenarioSubQuestion,
)
from app.modules.question_bank.models import Question, QuestionTopic, QuestionUsage
from app.modules.question_bank.models import QuestionOption as BankOption
from app.modules.question_bank.services.delivery_service import (
    deliverable_conditions,
    record_usages,
)

logger = get_logger(__name__)


class Uc02QuestionBankAdapter:
    """:class:`~...uc02.port.QuestionBankPort` over the in-process question bank."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- QuestionBankPort -------------------------------------------------

    def find_eligible_questions(self, query: QuestionQuery) -> list[BankQuestion]:
        statement = select(Question).options(
            selectinload(Question.options),
            selectinload(Question.topic_links).joinedload(QuestionTopic.topic),
        )

        if query.exclude_retired:
            for condition in deliverable_conditions(
                types=[str(item) for item in query.types] or None,
                topic_ids=list(query.topic_ids) or None,
            ):
                statement = statement.where(condition)
        else:
            # Retired questions are wanted, so only the caller's own filters apply.
            if query.types:
                statement = statement.where(Question.type.in_([str(item) for item in query.types]))
            if query.topic_ids:
                statement = statement.where(
                    Question.id.in_(
                        select(QuestionTopic.question_id).where(
                            QuestionTopic.topic_id.in_(list(query.topic_ids))
                        )
                    )
                )

        # Deterministic order so the pool handed to UC-03 is stable; any shuffling is UC-03's
        # decision, driven by the attempt's persisted seed.
        rows = self._session.scalars(statement.order_by(Question.seq)).all()
        return [self._to_port(row) for row in rows]

    def get_questions_by_ids(self, question_ids: Sequence[str]) -> list[BankQuestion]:
        if not question_ids:
            return []
        rows = self._session.scalars(
            select(Question)
            .options(
                selectinload(Question.options),
                selectinload(Question.topic_links).joinedload(QuestionTopic.topic),
            )
            # Deliberately unfiltered by status: an in-flight or historical attempt must always be
            # reconstructable, including questions retired since it started.
            .where(Question.id.in_(list(question_ids)))
            .order_by(Question.seq)
        ).all()
        return [self._to_port(row) for row in rows]

    def record_delivery(
        self,
        attempt_ref: str,
        delivered: Sequence[DeliveredQuestionRef],
        learner_ref: str | None = None,
    ) -> None:
        """Record the delivery against the bank, in the caller's transaction.

        Two deliberate decisions here:

        **Idempotent.** If this attempt already has usages recorded, nothing is written. Attempt
        creation is retried by clients, and a double-counted usage would make UC-02's
        reporting wrong
        in a way nobody would notice.

        **Never fatal.** A failure to write the bank's bookkeeping is logged and swallowed
        rather than
        propagated. The learner has a valid attempt with frozen questions; taking that away to
        protect a usage count would be the wrong trade. The reconciliation cost is a usage row, not
        a lost answer.
        """
        if not delivered:
            return

        existing = self._session.scalar(
            select(QuestionUsage.id).where(QuestionUsage.attempt_ref == attempt_ref).limit(1)
        )
        if existing is not None:
            return

        ordered = sorted(delivered, key=lambda entry: entry.position)
        questions = {
            question.id: question
            for question in self._session.scalars(
                select(Question)
                .options(selectinload(Question.options))
                .where(Question.id.in_([entry.question_id for entry in ordered]))
            ).all()
        }
        rows = [questions[entry.question_id] for entry in ordered if entry.question_id in questions]
        if len(rows) != len(ordered):
            logger.warning(
                "delivery.usage_questions_missing",
                extra={"attempt_ref": attempt_ref, "expected": len(ordered), "found": len(rows)},
            )

        try:
            record_usages(
                self._session,
                attempt_ref=attempt_ref,
                questions=rows,
                learner_ref=learner_ref,
                commit=False,
            )
        except Exception as exc:  # noqa: BLE001 - see the docstring: never fatal
            logger.error(
                "delivery.usage_record_failed",
                extra={"attempt_ref": attempt_ref, "question_count": len(rows)},
                exc_info=exc,
            )

    # ---- translation ------------------------------------------------------

    def _to_port(self, question: Question) -> BankQuestion:
        question_type = QuestionType(question.type)
        options = sorted(question.options, key=lambda option: option.position)
        topics = [link.topic for link in question.topic_links if link.topic is not None]

        common = {
            "question_id": question.id,
            "version": question.version,
            "type": question_type,
            "prompt": question.question_text,
            # UC-02's bank is not partitioned by quiz or course; topic is the scoping dimension.
            "quiz_id": None,
            "course_id": None,
            "topic_id": topics[0].id if topics else None,
            "points": float(question.points),
            "retired": question.status == QuestionStatus.RETIRED.value,
            "extra": {
                "reference": question.reference,
                "status": question.status,
                "difficulty": question.difficulty,
                "scoringStrategy": question.scoring_strategy,
                "topicIds": [topic.id for topic in topics],
                "topicNames": [topic.name for topic in topics],
            },
        }

        if question_type is QuestionType.DRAG_TO_ORDER:
            return BankQuestion(
                **common,
                order_items=tuple(_to_order_item(option) for option in options),
            )

        if question_type is QuestionType.SCENARIO:
            # One sub-question: UC-02's scenario is a vignette plus a single choice question.
            return BankQuestion(
                **common,
                scenario_text=question.scenario_text,
                sub_questions=(
                    ScenarioSubQuestion(
                        sub_question_id=f"{question.id}:1",
                        type=QuestionType.SINGLE_CHOICE,
                        prompt=question.question_text,
                        options=tuple(_to_option(option) for option in options),
                    ),
                ),
            )

        return BankQuestion(**common, options=tuple(_to_option(option) for option in options))


def _to_option(option: BankOption) -> QuestionOption:
    """A choice option. ``label`` is the bank's stable within-question key."""
    return QuestionOption(
        option_id=option.label, text=option.text, is_correct=bool(option.is_correct)
    )


def _to_order_item(option: BankOption) -> QuestionOrderItem:
    """An orderable item. ``correct_position`` is the answer key, never the presented order."""
    return QuestionOrderItem(
        item_id=option.label, text=option.text, correct_position=option.correct_position
    )
