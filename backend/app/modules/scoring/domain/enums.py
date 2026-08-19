"""UC-04's vocabulary.

The five question types, the statuses and the presentation modes are *not* here: they belong to the
shared kernel (``app.core.question_types``) and are re-exported so UC-04's own modules have one
import to reach for, exactly as UC-03 does.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.question_types import (
    QUESTION_TYPE_ORDER,
    QuestionPresentation,
    QuestionStatus,
    QuestionType,
)

__all__ = [
    "QUESTION_TYPE_ORDER",
    "QuestionPresentation",
    "QuestionStatus",
    "QuestionType",
    "ResultStatus",
    "QuestionOutcome",
    "AnswerKeySource",
    "ScoreAnomaly",
    "SCORING_ALGORITHM_VERSION",
]


#: Bumped whenever the scoring rules change. Stored on every result row, so a historical score can
#: always be explained by the rules that produced it — a score is only reproducible if you know
#: which algorithm ran.
SCORING_ALGORITHM_VERSION = 1


class ResultStatus(StrEnum):
    """Lifecycle of one attempt's result.

    Deliberately only two states. A scoring failure is ``PENDING_SCORE`` rather than a third
    "FAILED" state, because the learner's submission is unaffected and the run is retryable —
    calling it failed would suggest something was lost.
    """

    #: Submitted, but not yet scored. Rendered to a learner as "Submitted — Pending Score".
    PENDING_SCORE = "PENDING_SCORE"
    #: Scored and confirmed. Immutable from this point, by trigger as well as by service.
    SCORED = "SCORED"


#: The label a UI shows for each status. One definition, so the backend and the test UI agree.
RESULT_STATUS_LABELS: dict[ResultStatus, str] = {
    ResultStatus.PENDING_SCORE: "Submitted — Pending Score",
    ResultStatus.SCORED: "Scored",
}


class QuestionOutcome(StrEnum):
    """How one question turned out."""

    CORRECT = "CORRECT"
    #: Only reachable for MULTI_SELECT under a partial-credit strategy.
    PARTIALLY_CORRECT = "PARTIALLY_CORRECT"
    INCORRECT = "INCORRECT"
    #: No answer was saved, or the saved answer was cleared. Always zero marks.
    UNANSWERED = "UNANSWERED"
    #: The question could not be scored at all — see the result's anomalies.
    NOT_SCORED = "NOT_SCORED"


class AnswerKeySource(StrEnum):
    """Where the answer key that scored a question came from.

    Recorded per question because it is the difference between a score backed by the question bank's
    immutable version snapshot and one backed by the copy frozen onto the attempt. Both are
    trustworthy; which one was used is audit information, not a detail.
    """

    #: UC-02's immutable snapshot for the exact question version delivered. Preferred: it carries
    #: the configured scoring strategy, the per-incorrect deduction and the scenario primary answer.
    QUESTION_BANK_SNAPSHOT = "QUESTION_BANK_SNAPSHOT"
    #: The answer key frozen onto the attempt at delivery. Used when the bank's snapshot for that
    #: version is unavailable.
    ATTEMPT_SNAPSHOT = "ATTEMPT_SNAPSHOT"


class ScoreAnomaly(StrEnum):
    """Data defects that make a score untrustworthy.

    Every one of these blocks confirmation: the result stays ``PENDING_SCORE`` so nobody is shown a
    number that was computed from broken data, and the run can be retried once the data is fixed.
    """

    #: Neither the bank's snapshot nor the attempt's frozen copy yields a usable answer key.
    MISSING_ANSWER_KEY = "MISSING_ANSWER_KEY"
    #: The attempt's questions sum to a maximum of zero, so no percentage is definable.
    ZERO_MAXIMUM_MARKS = "ZERO_MAXIMUM_MARKS"
    #: A scenario has no single primary answer, so "score the primary answer" is undefined.
    AMBIGUOUS_PRIMARY_ANSWER = "AMBIGUOUS_PRIMARY_ANSWER"
    #: The stored answer does not fit the delivered question's shape.
    UNREADABLE_ANSWER = "UNREADABLE_ANSWER"
    #: The delivered question type is not one this scorer knows.
    UNSUPPORTED_QUESTION_TYPE = "UNSUPPORTED_QUESTION_TYPE"
    #: The attempt was delivered no questions at all.
    NO_QUESTIONS_DELIVERED = "NO_QUESTIONS_DELIVERED"
