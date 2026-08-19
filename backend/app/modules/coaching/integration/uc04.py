"""UC-04 (Answer Validation & Scoring) — the authoritative outcomes UC-07 consumes (§2, §20).

UC-07 runs no scoring. It asks UC-04 one question per delivered question — *did the learner get
this right?* — and treats the answer as final. Which questions enter the coaching review queue is
decided entirely by ``QuestionOutcome``; UC-07 has no rule of its own that could disagree (§36).

WHY THE ANSWER KEY IS ON THIS PORT AT ALL
-----------------------------------------
``QuestionResult.answer_key`` is the key UC-04 actually scored against, and real UC-04 records
carry it. It is modelled here deliberately, not by oversight, and it is the single most important
thing in this module:

* **It is never optional to defend against.** If UC-07 pretended upstream data were already clean,
  the sanitiser would be untested theatre. The key is present at the boundary, it is stripped at
  the boundary, and the tests prove the stripping by feeding a real key in (§13, §25, §26).
* **Nothing downstream may read it.** No service, no context builder and no prompt touches this
  field. The only code that looks at it is
  ``app.modules.coaching.domain.sanitizer``, which uses it to build the list of values that must
  *not* appear in the coaching context — the opposite of consuming it.

``UNANSWERED`` is kept distinct from ``INCORRECT`` because §20 is explicit: only questions the
authoritative result calls incorrect enter the review queue. A skipped question is not a
misconception to coach, and a question UC-04 could not score is certainly not one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.modules.coaching.integration.uc03 import QuestionType


class ScoreStatus(StrEnum):
    """UC-04's scoring lifecycle, mirrored exactly."""

    CONFIRMED = "CONFIRMED"
    PENDING = "PENDING"


class QuestionOutcome(StrEnum):
    """The outcome of one delivered question, as decided by UC-04."""

    CORRECT = "CORRECT"
    #: Answered, scored, and not fully correct — includes partial credit.
    INCORRECT = "INCORRECT"
    #: No answer was submitted.
    UNANSWERED = "UNANSWERED"
    #: Anomalous: could not be safely scored.
    INVALID = "INVALID"


#: The only outcome that puts a question into the coaching review queue (§20).
#:
#: UNANSWERED is excluded on purpose. A learner who ran out of time has no misconception to
#: uncover, and Socratic coaching on a blank is a conversation with nothing in it. If a deployment
#: decides a blank *is* a wrong answer, UC-04 is where that decision belongs — it can report the
#: question as INCORRECT and it will be coached, without UC-07 growing a scoring opinion (§36).
#:
#: INVALID is excluded because UC-04 explicitly could not judge the answer; coaching a learner
#: about a question the scoring engine refused to score would be teaching from a guess.
COACHABLE_OUTCOMES: frozenset[QuestionOutcome] = frozenset({QuestionOutcome.INCORRECT})


@dataclass(frozen=True, slots=True)
class QuestionResult:
    """UC-04's confirmed outcome for one delivered question."""

    question_id: str
    #: 1-based delivery position, so the review queue reads in the learner's order.
    position: int
    question_type: QuestionType
    outcome: QuestionOutcome
    maximum_marks: float | None = None
    awarded_marks: float | None = None
    scoring_rule: str | None = None
    anomaly_codes: tuple[str, ...] = field(default_factory=tuple)

    #: The answer key UC-04 scored against. **Read only by the sanitiser, and only to forbid it.**
    #: See the module docstring.
    answer_key: Mapping[str, Any] | None = None

    @property
    def coachable(self) -> bool:
        return self.outcome in COACHABLE_OUTCOMES


@dataclass(frozen=True, slots=True)
class AttemptScore:
    """UC-04's scoring result for a whole attempt.

    UC-07 reads ``status`` and ``question_results`` and reproduces nothing else. Totals are carried
    only so an eligibility response can say what the attempt scored without a second round trip;
    they are never recomputed, adjusted, or affected by anything coaching does (§4, §36).
    """

    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    status: ScoreStatus
    question_results: tuple[QuestionResult, ...] = field(default_factory=tuple)
    percentage: float | None = None
    total_marks: float | None = None
    maximum_marks: float | None = None
    confirmed_at: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.status is ScoreStatus.CONFIRMED

    def result_for(self, question_id: str) -> QuestionResult | None:
        return next(
            (item for item in self.question_results if item.question_id == question_id), None
        )

    def incorrect_results(self) -> tuple[QuestionResult, ...]:
        """Every question the authoritative result calls incorrect, in delivery order (§19, §20)."""
        return tuple(
            sorted(
                (item for item in self.question_results if item.coachable),
                key=lambda item: (item.position, item.question_id),
            )
        )


@runtime_checkable
class ScoringResultProvider(Protocol):
    """Read-only port onto UC-04.

    Implementations must not coerce a pending score into a confirmed one and must not fabricate
    outcomes. A transient failure should raise
    ``app.modules.coaching.domain.errors.UpstreamProviderUnavailableError``.
    """

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        """Return the scoring result for the attempt, or ``None`` when scoring has not run."""
        ...
