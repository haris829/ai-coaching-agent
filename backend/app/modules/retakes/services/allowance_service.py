"""Computing the attempt allowance (§1).

Thin by design: the arithmetic is a pure function in ``domain.allowance``, and this service only
does the part that needs the outside world — asking UC-03 how many attempts the learner has used,
asking UC-08's own store how many extra attempts an administrator has granted, and adding the
reservations UC-03 cannot see yet.

WHY THE USED COUNT IS THE MAXIMUM OF THREE NUMBERS
--------------------------------------------------
::

    attempts_used = max(UC-03's count, highest attempt number seen) + in-flight reservations

UC-05 already takes the greater of UC-03's count and the attempt's own number, so a lagging or
eventually-consistent count can never offer a learner an attempt the configuration does not allow.
UC-08 keeps that and adds the third term, which closes the window this module introduces: between
reserving a slot and UC-03 creating the attempt, the reservation is the only evidence the attempt
is coming. Counting it means two concurrent retakes cannot both see the same free attempt.

Everything here is deliberately learner-safe in the direction it rounds: an over-count refuses a
retake that could have been allowed, and an administrator can grant an extra attempt to correct
it. An under-count would hand out an attempt nobody authorised, and nothing corrects that.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.retakes.domain.allowance import (
    AttemptAllowance,
    compute_allowance,
    is_valid_maximum,
)
from app.modules.retakes.domain.anomalies import RetakeAnomaly, anomaly
from app.modules.retakes.domain.enums import RetakeAnomalyCode
from app.modules.retakes.domain.grants import total_granted_attempts
from app.modules.retakes.integration.uc03 import AttemptContext, AttemptProvider
from app.modules.retakes.repositories.protocols import GrantRepository, RetakeRequestRepository


class AttemptAllowanceService:
    def __init__(
        self,
        *,
        attempts: AttemptProvider,
        grants: GrantRepository,
        retakes: RetakeRequestRepository,
    ) -> None:
        self._attempts = attempts
        self._grants = grants
        self._retakes = retakes

    async def granted_attempts(self, learner_id: str, course_id: str, quiz_id: str) -> int:
        """Extra attempts an administrator has granted this learner for this quiz.

        Scoped to learner + course + quiz, so a grant never leaks to another learner, another
        course, or another quiz within the same course (§12).
        """
        grants = await self._grants.list_for_learner_quiz(learner_id, course_id, quiz_id)
        return total_granted_attempts(grants)

    async def attempts_used(
        self,
        *,
        learner_id: str,
        course_id: str,
        quiz_id: str,
        known_attempts: Sequence[AttemptContext] = (),
    ) -> int:
        """How many attempts the learner has consumed. See the module docstring."""
        counted = await self._attempts.count_used_attempts(learner_id, course_id, quiz_id)
        highest = max((attempt.attempt_number for attempt in known_attempts), default=0)
        reserved = await self._retakes.count_active_reservations(learner_id, quiz_id)
        return max(int(counted or 0), int(highest or 0)) + reserved

    async def compute(
        self,
        *,
        learner_id: str,
        course_id: str,
        quiz_id: str,
        maximum_attempts: int | None,
        known_attempts: Sequence[AttemptContext] = (),
    ) -> tuple[AttemptAllowance, tuple[RetakeAnomaly, ...]]:
        """The allowance, plus any anomaly about the configured maximum.

        ``maximum_attempts`` is supplied by the caller rather than read here, because *which*
        configuration version it comes from is a business decision that belongs with the
        eligibility rules — see ``eligibility_service``.
        """
        used = await self.attempts_used(
            learner_id=learner_id,
            course_id=course_id,
            quiz_id=quiz_id,
            known_attempts=known_attempts,
        )
        granted = await self.granted_attempts(learner_id, course_id, quiz_id)

        anomalies: tuple[RetakeAnomaly, ...] = ()
        if not is_valid_maximum(maximum_attempts):
            # Reported, not enforced: a broken maximum is a configuration defect and must not
            # silently tell a learner they have no attempts left. Same choice as UC-05.
            anomalies = (
                anomaly(
                    RetakeAnomalyCode.INVALID_ATTEMPT_ALLOWANCE,
                    "The configured maximum attempts is not a usable positive integer; "
                    "attempts are reported as unlimited until it is corrected.",
                    quiz_id=quiz_id,
                    maximum_attempts=maximum_attempts,
                ),
            )

        allowance = compute_allowance(
            maximum_attempts=maximum_attempts,
            attempts_used=used,
            granted_attempts=granted,
        )
        return allowance, anomalies
