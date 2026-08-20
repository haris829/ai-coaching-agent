"""Coaching authorisation (§7, §8, §9).

The gate in ``domain.eligibility`` is a pure function over facts. This service is what fetches
those facts and turns a refusal into the right error.

It exists as its own class for one reason: **every coaching operation goes through it.** Starting a
session, sending a message, switching mode, retrying, reading the review queue, moving to the next
question — all of them call ``authorize`` first, with the current state of the world, not with
something decided earlier and cached. That is what §8 asks for when it says the backend must
enforce the rule rather than relying on a hidden button: there is no method on any coaching service
that reaches a model without passing through this file.

RE-CHECKED EVERY TIME, NOT ONLY AT THE START
--------------------------------------------
A session that was legitimately opened is not a licence. If the feedback report is withdrawn, or
the attempt is re-opened, or a re-score turns a wrong answer into a right one, the next message in
an already-running conversation is refused. Checking only at ``start`` would leave a live channel
into the model attached to state that has since changed.

FAILURES ARE UPSTREAM FAILURES
------------------------------
If UC-03, UC-04 or UC-06 cannot be reached, their ports raise ``ProviderUnavailableError`` and it
propagates. That is deliberate: "we could not confirm this attempt was submitted" must never
degrade into "coaching allowed".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import AppError
from app.modules.coaching.domain.eligibility import Eligibility, can_start_coaching
from app.modules.coaching.domain.enums import EligibilityCode
from app.modules.coaching.domain.errors import (
    AttemptNotFoundError,
    AttemptNotSubmittedError,
    CoachingServiceUnavailableError,
    FeedbackUnavailableError,
    FormalAssessmentInProgressError,
    LearnerNotAuthorizedError,
    QuestionNotInAttemptError,
    QuestionNotIncorrectError,
    ScoreNotConfirmedError,
)
from app.modules.coaching.integration.llm import CoachingLLM
from app.modules.coaching.integration.uc03 import AttemptContext, AttemptProvider
from app.modules.coaching.integration.uc04 import AttemptScore, ScoringResultProvider
from app.modules.coaching.integration.uc06 import AttemptFeedback, FeedbackProvider
from app.modules.coaching.integration.uc09 import (
    FormalAssessmentPolicyPort,
    UnrestrictedFormalAssessmentPolicy,
)


@dataclass(frozen=True, slots=True)
class GateResult:
    """The verdict plus everything that was read to reach it.

    Carried so a caller that has just been authorised does not read UC-03, UC-04 and UC-06 a second
    time to do the work — and, more importantly, so it cannot accidentally act on a *different*
    read of them than the one the gate approved.
    """

    eligibility: Eligibility
    attempt: AttemptContext | None = None
    score: AttemptScore | None = None
    feedback: AttemptFeedback | None = None

    @property
    def allowed(self) -> bool:
        return self.eligibility.coaching_available


def eligibility_error(
    eligibility: Eligibility, *, learner_id: str, attempt_id: str, question_id: str | None
) -> AppError:
    """Map a refusal onto the error the API should raise (§29).

    One place, so an HTTP status can never drift from the domain reason that produced it.
    """
    details = eligibility.details
    code = eligibility.code

    if code is EligibilityCode.ATTEMPT_NOT_FOUND:
        return AttemptNotFoundError(attempt_id)
    if code is EligibilityCode.NOT_ATTEMPT_OWNER:
        return LearnerNotAuthorizedError(attempt_id, learner_id)
    if code is EligibilityCode.FORMAL_ASSESSMENT_IN_PROGRESS:
        return FormalAssessmentInProgressError(attempt_id, learner_id)
    if code is EligibilityCode.ATTEMPT_NOT_SUBMITTED:
        return AttemptNotSubmittedError(attempt_id, str(details.get("attemptStatus")))
    if code is EligibilityCode.SCORE_NOT_CONFIRMED:
        return ScoreNotConfirmedError(attempt_id, details.get("scoreStatus"))
    if code is EligibilityCode.FEEDBACK_UNAVAILABLE:
        return FeedbackUnavailableError(attempt_id, details.get("feedbackStatus"))
    if code is EligibilityCode.QUESTION_NOT_IN_ATTEMPT:
        return QuestionNotInAttemptError(attempt_id, str(question_id))
    if code is EligibilityCode.QUESTION_NOT_INCORRECT:
        return QuestionNotIncorrectError(
            attempt_id, str(question_id), str(details.get("outcome"))
        )
    return CoachingServiceUnavailableError(reason=code.value)


class CoachingAuthorizer:
    """Reads the upstream state and applies the gate."""

    def __init__(
        self,
        *,
        attempts: AttemptProvider,
        scores: ScoringResultProvider,
        feedback: FeedbackProvider,
        llm: CoachingLLM,
        formal_assessment: FormalAssessmentPolicyPort | None = None,
    ) -> None:
        self._attempts = attempts
        self._scores = scores
        self._feedback = feedback
        self._llm = llm
        # Defaulted rather than required, so a deployment without UC-09 keeps working and every
        # existing caller of this constructor stays valid. See ``integration/uc09.py`` for why
        # "allow" is the honest default here and "refuse" is the honest default everywhere else.
        self._formal_assessment = formal_assessment or UnrestrictedFormalAssessmentPolicy()

    async def evaluate(
        self,
        *,
        learner_id: str,
        attempt_id: str,
        question_id: str | None = None,
        require_service: bool = True,
    ) -> GateResult:
        """Apply the gate without raising. Used by the eligibility endpoint (§10, §31).

        ``require_service=False`` skips the AI availability probe. Reads that do not talk to a
        model — the review queue, a session's stored state — use it, so an AI outage does not stop
        a learner from seeing which questions they got wrong.
        """
        attempt = await self._attempts.get_attempt(attempt_id)

        score: AttemptScore | None = None
        feedback: AttemptFeedback | None = None
        formal_in_progress = False
        # Only read further once the attempt is known to be this learner's: an ownership failure
        # must not be preceded by queries that could differ observably for someone else's attempt.
        if attempt is not None and attempt.learner_id == learner_id:
            score = await self._scores.get_score(attempt_id)
            feedback = await self._feedback.get_attempt_feedback(attempt_id)
            # UC-09 §7, read on every operation like everything else here. Learner-scoped: the
            # question is whether *this learner* is mid-exam, not whether this attempt is one.
            formal_in_progress = (
                await self._formal_assessment.get_status_for_learner(learner_id)
            ).in_progress

        service_available = True
        if require_service:
            service_available = await self._service_available()

        eligibility = can_start_coaching(
            learner_id=learner_id,
            attempt_id=attempt_id,
            attempt=attempt,
            score=score,
            feedback=feedback,
            question_id=question_id,
            service_available=service_available,
            formal_assessment_in_progress=formal_in_progress,
        )
        return GateResult(
            eligibility=eligibility, attempt=attempt, score=score, feedback=feedback
        )

    async def authorize(
        self,
        *,
        learner_id: str,
        attempt_id: str,
        question_id: str | None = None,
        require_service: bool = True,
    ) -> GateResult:
        """Apply the gate and raise the mapped error on refusal."""
        result = await self.evaluate(
            learner_id=learner_id,
            attempt_id=attempt_id,
            question_id=question_id,
            require_service=require_service,
        )
        if not result.allowed:
            raise eligibility_error(
                result.eligibility,
                learner_id=learner_id,
                attempt_id=attempt_id,
                question_id=question_id,
            )
        return result

    async def _service_available(self) -> bool:
        """Probe the AI service, treating a broken probe as unavailability.

        An implementation is asked not to raise from ``is_available``; if one does anyway, the
        honest reading is that the service is not healthy — never that it is (§27).
        """
        try:
            return bool(await self._llm.is_available())
        except Exception:  # noqa: BLE001 - a failing probe is a negative answer, not a 500.
            return False
