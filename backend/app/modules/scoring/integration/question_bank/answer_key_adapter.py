"""UC-02's question bank, seen through UC-04's answer-key port.

Reads ``qb_question_snapshots`` -- the bank's immutable, one-row-per-version frozen copy of a
question -- for the exact versions an attempt was delivered, and translates each into UC-04's
:class:`AnswerKey`.

Why the snapshot and not the live question: the snapshot for version *n* never changes, so a key
resolved from it produces the same score today, after tomorrow's edit, and after the question is
retired. Reading the live ``qb_questions`` row would make a historical score a function of the
bank's current state, which is exactly the property UC-02 built snapshots to avoid.

Parsing goes through UC-02's own :func:`parse_snapshot_view`, so the payload format has one reader.
The authored scoring strategy is translated by
:mod:`app.modules.scoring.integration.marking_policy`, the only place in UC-04 that knows those
names.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.question_bank.domain.snapshots import load_payload, parse_snapshot_view
from app.modules.question_bank.models import QuestionSnapshot
from app.modules.scoring.domain.answer_key import AnswerKey, KeyOption
from app.modules.scoring.domain.enums import AnswerKeySource, QuestionType
from app.modules.scoring.integration.marking_policy import translate
from app.modules.scoring.integration.question_bank.port import QuestionVersionRef

logger = get_logger(__name__)


class QuestionBankAnswerKeyAdapter:
    """:class:`~...question_bank.port.AnswerKeyPort` over the in-process question bank."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- AnswerKeyPort ----------------------------------------------------

    def find_answer_keys(
        self, refs: Sequence[QuestionVersionRef]
    ) -> dict[QuestionVersionRef, AnswerKey]:
        wanted = {QuestionVersionRef(str(ref.question_id), int(ref.version)) for ref in refs}
        if not wanted:
            return {}

        rows = self._session.scalars(
            select(QuestionSnapshot).where(
                tuple_(QuestionSnapshot.question_id, QuestionSnapshot.version).in_(
                    [(ref.question_id, ref.version) for ref in wanted]
                )
            )
        ).all()

        keys: dict[QuestionVersionRef, AnswerKey] = {}
        for row in rows:
            ref = QuestionVersionRef(row.question_id, row.version)
            key = self._to_key(ref, row)
            if key is not None:
                keys[ref] = key

        missing = sorted(wanted - set(keys))
        if missing:
            # Not an error: UC-04 falls back to the attempt's frozen copy. Logged because a bank
            # snapshot that cannot be resolved is worth knowing about operationally.
            logger.info(
                "scoring.answer_key_snapshot_missing",
                extra={"count": len(missing), "questionIds": [ref.question_id for ref in missing]},
            )
        return keys

    # ---- translation ------------------------------------------------------

    def _to_key(self, ref: QuestionVersionRef, row: QuestionSnapshot) -> AnswerKey | None:
        payload = load_payload(row.payload)
        view = parse_snapshot_view(payload)
        if view is None:
            # A corrupt payload must not crash scoring; the fallback key takes over.
            logger.error(
                "scoring.answer_key_snapshot_unreadable",
                extra={"questionId": ref.question_id, "version": ref.version},
            )
            return None

        try:
            question_type = QuestionType(str(row.type))
        except ValueError:  # pragma: no cover - the bank validates its own types
            return None

        topics = tuple(str(name) for name in (payload.get("topics") or []) if name)

        return AnswerKey(
            question_id=ref.question_id,
            question_version=ref.version,
            question_type=question_type,
            max_marks=float(view.points),
            marking_policy=translate(view.scoring_strategy),
            deduction_per_incorrect=float(view.penalty_per_incorrect or 0.0),
            options=tuple(
                KeyOption(
                    option_id=option.label,
                    text=option.text,
                    is_correct=option.is_correct,
                    is_primary=option.is_primary,
                    correct_position=option.correct_position,
                )
                for option in view.options
            ),
            source=AnswerKeySource.QUESTION_BANK_SNAPSHOT,
            explanation=view.explanation,
            topics=topics,
            reference=row.reference,
        )
