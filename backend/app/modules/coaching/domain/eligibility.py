"""The coaching authorisation gate (§7, §8, §9, §20).

    attempt exists → belongs to this learner → submitted → scored → feedback released
                   → question is in the attempt → question was answered incorrectly
                   → coaching service reachable → coaching may begin

A pure function over the upstream facts. It answers one question — may this learner be coached on
this question right now? — and it answers "no" with a code saying which condition failed, so the
caller can render a refusal instead of guessing (§27).

THE ORDER OF THE CHECKS IS PART OF THE DESIGN
---------------------------------------------
**Ownership before anything else.** A learner who guesses another learner's attempt id learns only
that it is not theirs. If submission or scoring were checked first, the refusals would differ by
attempt state and the endpoint would become a way to probe someone else's progress (§9).

**Service availability last.** A question that was answered correctly is refused as
``QUESTION_NOT_INCORRECT`` even during an AI outage, because that refusal is permanent and the
outage is not. Reporting the temporary problem first would tell a client to retry something that
will never succeed.

WHY THIS LIVES IN THE DOMAIN
----------------------------
§8 is explicit that hiding a button is not protection. This function has no I/O, no framework and
no knowledge of HTTP: it is called by the service on *every* coaching operation — starting a
session, sending a message, moving through the review queue — so there is no route to the model
that bypasses it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.coaching.domain.enums import (
    TRANSIENT_ELIGIBILITY_CODES,
    EligibilityCode,
)
from app.modules.coaching.integration.uc03 import AttemptContext
from app.modules.coaching.integration.uc04 import AttemptScore, QuestionResult
from app.modules.coaching.integration.uc06 import AttemptFeedback


@dataclass(frozen=True, slots=True)
class Eligibility:
    """The gate's verdict.

    ``coaching_available`` is the field a frontend reads (§4, §10): the backend states whether the
    "Review with Larry" action may be offered, and the frontend decides how to render it.
    """

    code: EligibilityCode
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    #: The authoritative UC-04 result for the question, when one was asked about and found. Carried
    #: so callers do not re-read UC-04 to discover what the gate already resolved.
    result: QuestionResult | None = None

    @property
    def coaching_available(self) -> bool:
        return self.code is EligibilityCode.ELIGIBLE

    @property
    def retryable(self) -> bool:
        """Whether asking again later could produce a different answer."""
        return self.code in TRANSIENT_ELIGIBILITY_CODES

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "coaching_available": self.coaching_available,
            "reason": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload


_ELIGIBLE = Eligibility(code=EligibilityCode.ELIGIBLE)


def can_start_coaching(
    *,
    learner_id: str,
    attempt_id: str,
    attempt: AttemptContext | None,
    score: AttemptScore | None,
    feedback: AttemptFeedback | None,
    question_id: str | None = None,
    service_available: bool = True,
) -> Eligibility:
    """Decide whether coaching may begin (§9).

    ``question_id`` is optional so the same gate serves both questions a caller asks:

    * *may this learner be coached on this attempt at all?* — the attempt-level checks only, which
      is what the eligibility and review-queue endpoints need;
    * *may this learner be coached on this question?* — all of them.

    Passing ``None`` for ``question_id`` never *weakens* the gate; it stops before the two
    question-level checks, and every caller that goes on to open a session passes one.
    """
    if attempt is None:
        return Eligibility(
            code=EligibilityCode.ATTEMPT_NOT_FOUND,
            message="No such attempt exists.",
            details={"attemptId": attempt_id},
        )

    if attempt.learner_id != learner_id:
        # Deliberately says nothing about the attempt's state — see the module docstring.
        return Eligibility(
            code=EligibilityCode.NOT_ATTEMPT_OWNER,
            message="This attempt does not belong to the requesting learner.",
            details={"attemptId": attempt_id},
        )

    if not attempt.submitted:
        return Eligibility(
            code=EligibilityCode.ATTEMPT_NOT_SUBMITTED,
            message=(
                "Coaching is available only after the quiz has been submitted. This attempt is "
                "still in progress."
            ),
            details={"attemptStatus": attempt.status.value},
        )

    if score is None or not score.is_confirmed:
        return Eligibility(
            code=EligibilityCode.SCORE_NOT_CONFIRMED,
            message=(
                "Coaching requires a confirmed scoring result, which decides what counts as an "
                "incorrect answer."
            ),
            details={"scoreStatus": score.status.value if score else None},
        )

    if feedback is None or not feedback.available:
        return Eligibility(
            code=EligibilityCode.FEEDBACK_UNAVAILABLE,
            message=(
                "Coaching becomes available once the detailed feedback report has been released "
                "for this attempt."
            ),
            details={"feedbackStatus": feedback.status.value if feedback else None},
        )

    result: QuestionResult | None = None
    if question_id is not None:
        result = score.result_for(question_id)
        if result is None:
            return Eligibility(
                code=EligibilityCode.QUESTION_NOT_IN_ATTEMPT,
                message="This question is not part of the requested attempt.",
                details={"attemptId": attempt_id, "questionId": question_id},
            )

        if not result.coachable:
            return Eligibility(
                code=EligibilityCode.QUESTION_NOT_INCORRECT,
                message=(
                    "Coaching is offered only for questions that were answered incorrectly."
                ),
                details={"questionId": question_id, "outcome": result.outcome.value},
                result=result,
            )

    if not service_available:
        return Eligibility(
            code=EligibilityCode.SERVICE_UNAVAILABLE,
            message=(
                "AI coaching is temporarily unavailable. Your quiz result and feedback are "
                "unaffected."
            ),
            details={"attemptId": attempt_id},
            result=result,
        )

    if result is None:
        return _ELIGIBLE
    return Eligibility(code=EligibilityCode.ELIGIBLE, result=result)
