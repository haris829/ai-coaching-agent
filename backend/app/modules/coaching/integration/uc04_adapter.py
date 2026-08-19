"""UC-04's confirmed scores, seen through UC-07's port.

UC-07 runs no scoring. It asks UC-04 one question per delivered question — *did the learner get
this right?* — and treats the answer as final. Which questions enter the coaching review queue is
decided entirely here; UC-07 has no rule that could disagree with the score the learner was shown.

Read-only: two ``SELECT``s and a translation. There is no path from here to writing a mark.

THE OUTCOME MAPPING IS THE WHOLE INTERFACE
------------------------------------------
UC-04 names five outcomes and UC-07 four. The translation is where "what counts as a wrong answer
worth coaching" is decided, so it is stated once, in a table, rather than inferred at three call
sites:

======================  =============  =========================================================
UC-04                   UC-07          Why
======================  =============  =========================================================
``CORRECT``             ``CORRECT``    Nothing to coach. Never enters the queue.
``PARTIALLY_CORRECT``   ``INCORRECT``  Answered, scored, not fully right — exactly a misconception
                                       worth a conversation. Coaching it is also safe: the coach is
                                       never told *which* of the learner's ticks were right (§12).
``INCORRECT``           ``INCORRECT``  The main case.
``UNANSWERED``          ``UNANSWERED`` A learner who ran out of time has no misconception to
                                       uncover; Socratic coaching on a blank is a conversation with
                                       nothing in it.
``NOT_SCORED``          ``INVALID``    UC-04 explicitly could not judge the answer. Coaching from
                                       a guess would be teaching from a guess.
======================  =============  =========================================================

Only ``INCORRECT`` is coachable, and that rule lives in ``integration/uc04.py`` beside the outcome
enum. If a deployment decides a blank *is* a wrong answer, UC-04 is where that belongs — it can
report the question as ``INCORRECT`` and it is coached, without UC-07 growing a scoring opinion.

WHY THIS ADAPTER HANDS OVER THE ANSWER KEY
------------------------------------------
Because UC-04 has one, and a sanitiser that is only ever fed clean input has not been tested.
``QuestionResult.answer_key`` is assembled below from the three answer-bearing columns UC-04 froze
— the correct-answer display, the per-option breakdown and the authored explanation — and it is read
by exactly one piece of code: ``domain.sanitizer.forbidden_values``, which uses it to build the list
of values that must **not** appear in the coaching context. Nothing else in UC-07 touches the field.
Stripping it here instead would make the sanitiser's guarantee unverifiable, which is the opposite
of the goal.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.question_types import QuestionType
from app.core.time import iso_or_none
from app.modules.coaching.domain.errors import UpstreamProviderUnavailableError
from app.modules.coaching.integration.uc04 import (
    AttemptScore,
    QuestionOutcome,
    QuestionResult,
    ScoreStatus,
)
from app.modules.coaching.repositories.sqlalchemy import offload
from app.modules.scoring.domain.enums import (
    QuestionOutcome as ScoringOutcome,
)
from app.modules.scoring.domain.enums import (
    ResultStatus,
)
from app.modules.scoring.models import AttemptResult, QuestionScoreRow

#: See the table in the module docstring.
_OUTCOMES: dict[ScoringOutcome, QuestionOutcome] = {
    ScoringOutcome.CORRECT: QuestionOutcome.CORRECT,
    ScoringOutcome.PARTIALLY_CORRECT: QuestionOutcome.INCORRECT,
    ScoringOutcome.INCORRECT: QuestionOutcome.INCORRECT,
    ScoringOutcome.UNANSWERED: QuestionOutcome.UNANSWERED,
    ScoringOutcome.NOT_SCORED: QuestionOutcome.INVALID,
}


class ScoringCoachingAdapter:
    """``ScoringResultProvider`` over UC-04's tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- ScoringResultProvider --------------------------------------------

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        return await offload(self._get_score, attempt_id)

    # ---- synchronous body -------------------------------------------------

    def _get_score(self, attempt_id: str) -> AttemptScore | None:
        try:
            result = self._session.scalar(
                select(AttemptResult).where(AttemptResult.attempt_id == attempt_id)
            )
            if result is None:
                # Scoring has not run. Reported as absence, which the gate reads as
                # SCORE_NOT_CONFIRMED — never coerced into a confirmed score.
                return None
            rows = self._session.scalars(
                select(QuestionScoreRow)
                .where(QuestionScoreRow.result_id == result.id)
                .order_by(QuestionScoreRow.position)
            ).all()
        except SQLAlchemyError as exc:
            raise UpstreamProviderUnavailableError(
                "uc04", attempt_id=attempt_id, cause=exc
            ) from exc

        return AttemptScore(
            attempt_id=result.attempt_id,
            learner_id=result.learner_id,
            course_id=result.course_id,
            quiz_id=result.quiz_id,
            status=self._status(result.status),
            question_results=tuple(self._to_result(row) for row in rows),
            percentage=result.percentage,
            total_marks=result.total_marks,
            maximum_marks=result.maximum_marks,
            confirmed_at=iso_or_none(result.scored_at),
        )

    # ---- translation ------------------------------------------------------

    @staticmethod
    def _status(raw: str) -> ScoreStatus:
        return (
            ScoreStatus.CONFIRMED if raw == ResultStatus.SCORED.value else ScoreStatus.PENDING
        )

    def _to_result(self, row: QuestionScoreRow) -> QuestionResult:
        return QuestionResult(
            question_id=row.question_id,
            position=row.position,
            question_type=QuestionType(row.question_type),
            outcome=_OUTCOMES.get(ScoringOutcome(row.outcome), QuestionOutcome.INVALID),
            maximum_marks=row.maximum_marks,
            awarded_marks=row.awarded_marks,
            anomaly_codes=(row.anomaly,) if row.anomaly else (),
            answer_key=self._answer_key(row),
        )

    @staticmethod
    def _answer_key(row: QuestionScoreRow) -> dict[str, Any] | None:
        """The answer key UC-04 scored against, for the sanitiser to forbid.

        ``type`` and ``scoring_rule`` are named exactly as the sanitiser's list of *structural*
        answer-key fields expects, so their values — ``MULTI_SELECT``, a key source — are not
        mistaken for answer-bearing content and do not make the contamination scanner fire on
        ``question_type`` on every single run.

        ``explanation`` is UC-04's frozen copy of the *authored* explanation, or NULL when the
        question carries none. It is answer-bearing prose, so it belongs in the forbidden set: a
        misconception note that quoted it would otherwise reach the coach.
        """
        key: dict[str, Any] = {
            "type": row.question_type,
            "scoring_rule": row.answer_key_source,
            "correct_answer": row.correct_answer_display,
            "option_marks": row.option_marks,
            "explanation": row.explanation,
        }
        populated = {name: value for name, value in key.items() if value is not None}
        # Only the two structural fields survived: UC-04 recorded no answer key for this question,
        # which is an anomaly it will have flagged, not something to pretend we have.
        if not set(populated) - {"type", "scoring_rule"}:
            return None
        return populated
