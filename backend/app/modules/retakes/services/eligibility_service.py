"""Retake eligibility, and the configuration version a retake would lock (§1, §2, §4).

One loader, used by two callers. ``load`` gathers everything a retake decision needs; the
eligibility endpoint renders it, and ``RetakeService`` re-runs the same call before creating
anything. That is deliberate: if the read-only check and the enforcement path used different code,
the button and the rule could disagree, and §13 would be satisfied only by coincidence.

WHICH CONFIGURATION VERSION SUPPLIES THE MAXIMUM (§4, first half)
-----------------------------------------------------------------
The version **locked to the learner's most recent attempt** — never today's active version. This
is UC-05's rule, adopted unchanged: an administrator who lowers the attempt limit after a learner
started must not retroactively strip an attempt the learner already held. If that version cannot
be read, the retake is refused rather than allowed against an assumed limit.

WHICH CONFIGURATION VERSION THE RETAKE LOCKS (§4, second half)
--------------------------------------------------------------
A retake is a new, independent attempt, and UC-03's implemented rule for any new attempt is to
read the version active at creation and lock it. UC-08 follows that rule by default and records
which of two things happened:

* ``CARRIED_FORWARD`` — the active version is the same one the previous attempt ran under, which
  is the ordinary case: no publication has happened in between, so nothing changes;
* ``ADVANCED_TO_ACTIVE`` — UC-01 has published a newer version since, and the retake locks it,
  exactly as a first attempt started today would.

Neither is accidental, and neither touches the previous attempt: UC-01's versions are immutable and
the historical attempt keeps its own. A deployment whose business rules require retakes to stay on
the previous version sets ``RETAKE_CONFIGURATION_POLICY=CARRY_FORWARD_PREVIOUS``, which records
``PINNED_TO_PREVIOUS``. The setting exists so the choice is a stated policy rather than an
implementation detail nobody noticed.

A resolved version is always checked to belong to the same quiz and course as the previous attempt.
A version for a different quiz is not a legitimate advance, and refusing it is what makes
"a retake cannot switch configuration by accident" a checked property rather than an aspiration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.logging import get_logger
from app.modules.retakes.domain.allowance import AttemptAllowance, compute_allowance
from app.modules.retakes.domain.anomalies import RetakeAnomaly
from app.modules.retakes.domain.eligibility import (
    RetakeBlocker,
    RetakeEligibility,
    blocker,
    determine_state,
)
from app.modules.retakes.domain.enums import (
    ConfigurationVersionSource,
    RetakeBlockerCode,
    RetakeState,
)
from app.modules.retakes.domain.errors import QuizNotFoundError
from app.modules.retakes.domain.requests import RetakeRequest
from app.modules.retakes.integration.uc01 import ConfigurationProvider, QuizConfigurationVersion
from app.modules.retakes.integration.uc03 import AttemptContext, AttemptProvider
from app.modules.retakes.repositories.protocols import RetakeRequestRepository
from app.modules.retakes.services.allowance_service import AttemptAllowanceService

logger = get_logger(__name__)

CARRY_FORWARD_POLICY = "CARRY_FORWARD_PREVIOUS"


@dataclass(frozen=True, slots=True)
class RetakeContext:
    """Everything a retake decision needs, read once.

    Held as a value object so ``RetakeService`` does not re-read the upstream modules between
    deciding and acting — a second read could return a different active version, and the retake
    would then be created against a configuration the eligibility check never saw.
    """

    learner_id: str
    quiz_id: str
    course_id: str
    attempts: tuple[AttemptContext, ...]
    allowance: AttemptAllowance
    blockers: tuple[RetakeBlocker, ...] = field(default_factory=tuple)
    anomalies: tuple[RetakeAnomaly, ...] = field(default_factory=tuple)
    previous_attempt: AttemptContext | None = None
    open_attempt: AttemptContext | None = None
    #: The version the most recent attempt ran under; supplies the maximum attempts.
    locked_configuration: QuizConfigurationVersion | None = None
    #: The version the retake would lock, per the configured policy.
    target_configuration: QuizConfigurationVersion | None = None
    configuration_version_source: ConfigurationVersionSource | None = None
    in_flight_retake: RetakeRequest | None = None

    @property
    def next_attempt_number(self) -> int:
        """The slot a retake would take.

        Derived from the highest attempt number the learner holds rather than from a count, so a
        gap in numbering (a purged attempt, a migration) cannot make two retakes collide.
        """
        highest = max((attempt.attempt_number for attempt in self.attempts), default=0)
        reserved = max(
            (
                request.attempt_number
                for request in (self.in_flight_retake,)
                if request is not None and request.holds_attempt_slot
            ),
            default=0,
        )
        return max(highest, reserved) + 1

    @property
    def can_retake(self) -> bool:
        return not self.blockers and self.target_configuration is not None


class RetakeEligibilityService:
    def __init__(
        self,
        *,
        attempts: AttemptProvider,
        configurations: ConfigurationProvider,
        retakes: RetakeRequestRepository,
        allowances: AttemptAllowanceService,
        settings: Settings,
    ) -> None:
        self._attempts = attempts
        self._configurations = configurations
        self._retakes = retakes
        self._allowances = allowances
        self._settings = settings

    # ------------------------------------------------------------ loading

    async def load(self, learner_id: str, quiz_id: str) -> RetakeContext:
        """Read the full retake context for a learner and quiz."""
        availability = await self._configurations.get_quiz_availability(quiz_id)
        if availability is None:
            # A quiz nobody can describe is not a quiz a retake can be created for. Distinct from
            # "not available", which is a state of a real quiz.
            raise QuizNotFoundError(quiz_id)

        course_id = availability.course_id
        blockers: list[RetakeBlocker] = []
        anomalies: list[RetakeAnomaly] = []

        if not availability.available:
            blockers.append(
                blocker(
                    RetakeBlockerCode.QUIZ_NOT_AVAILABLE,
                    "The quiz is not currently available for attempts.",
                    reason=availability.reason,
                )
            )

        attempts = await self._attempts.list_attempts(learner_id, quiz_id)
        previous = _latest_retakeable(attempts)
        open_attempt = _first_open(attempts) or await self._attempts.find_open_attempt(
            learner_id, quiz_id
        )

        if open_attempt is not None:
            blockers.append(
                blocker(
                    RetakeBlockerCode.ATTEMPT_IN_PROGRESS,
                    "An attempt at this quiz is already in progress.",
                    open_attempt_id=open_attempt.attempt_id,
                    status=str(open_attempt.status),
                )
            )

        if not attempts:
            blockers.append(
                blocker(
                    RetakeBlockerCode.NO_COMPLETED_ATTEMPT,
                    "The learner has no attempt at this quiz to retake.",
                )
            )
        elif previous is None and open_attempt is None:
            blockers.append(
                blocker(
                    RetakeBlockerCode.PREVIOUS_ATTEMPT_NOT_COMPLETE,
                    "No attempt at this quiz has been submitted yet.",
                )
            )

        in_flight = _in_flight(await self._retakes.list_for_learner_quiz(learner_id, quiz_id))
        if in_flight is not None:
            blockers.append(
                blocker(
                    RetakeBlockerCode.RETAKE_IN_PROGRESS,
                    "A retake is already being created for this learner.",
                    retake_id=in_flight.retake_id,
                )
            )

        # ---- configuration versions -------------------------------------
        locked = None
        if previous is not None:
            locked = await self._configurations.get_locked_configuration(
                previous.configuration_version_id
            )
            if locked is None:
                blockers.append(
                    blocker(
                        RetakeBlockerCode.CONFIGURATION_UNAVAILABLE,
                        "The configuration version the previous attempt ran under could not be "
                        "read, so the attempt limit cannot be established.",
                        configuration_version_id=previous.configuration_version_id,
                    )
                )

        target, source, config_blocker = await self._resolve_target_configuration(
            quiz_id=quiz_id, course_id=course_id, previous=previous, locked=locked
        )
        if config_blocker is not None:
            blockers.append(config_blocker)

        # ---- allowance ---------------------------------------------------
        if locked is not None:
            allowance, allowance_anomalies = await self._allowances.compute(
                learner_id=learner_id,
                course_id=course_id,
                quiz_id=quiz_id,
                maximum_attempts=locked.maximum_attempts,
                known_attempts=attempts,
            )
            anomalies.extend(allowance_anomalies)
        else:
            # No readable limit. The counts are still reported honestly, but the maximum is not
            # guessed — a CONFIGURATION_UNAVAILABLE or NO_COMPLETED_ATTEMPT blocker is already
            # present, so nothing is decided from this allowance.
            used = await self._allowances.attempts_used(
                learner_id=learner_id,
                course_id=course_id,
                quiz_id=quiz_id,
                known_attempts=attempts,
            )
            granted = await self._allowances.granted_attempts(learner_id, course_id, quiz_id)
            allowance = compute_allowance(
                maximum_attempts=None, attempts_used=used, granted_attempts=granted
            )

        if not allowance.has_available_attempts:
            blockers.append(
                blocker(
                    RetakeBlockerCode.NO_ATTEMPTS_REMAINING,
                    "No attempts remain for this quiz.",
                    maximum_attempts=allowance.maximum_attempts,
                    granted_attempts=allowance.granted_attempts,
                    attempts_used=allowance.attempts_used,
                )
            )

        return RetakeContext(
            learner_id=learner_id,
            quiz_id=quiz_id,
            course_id=course_id,
            attempts=attempts,
            allowance=allowance,
            blockers=tuple(blockers),
            anomalies=tuple(anomalies),
            previous_attempt=previous,
            open_attempt=open_attempt,
            locked_configuration=locked,
            target_configuration=target,
            configuration_version_source=source,
            in_flight_retake=in_flight,
        )

    async def check(self, learner_id: str, quiz_id: str) -> RetakeEligibility:
        """The read-only answer §2 requires, in one object."""
        return self.describe(await self.load(learner_id, quiz_id))

    def describe(self, context: RetakeContext) -> RetakeEligibility:
        """Render a loaded context as an eligibility answer.

        Separated from :meth:`load` so retake creation can report the same eligibility it acted on
        without a second round of upstream reads.
        """
        state = determine_state(context.allowance, context.blockers)
        exhausted = any(
            item.code is RetakeBlockerCode.NO_ATTEMPTS_REMAINING for item in context.blockers
        )
        return RetakeEligibility(
            learner_id=context.learner_id,
            quiz_id=context.quiz_id,
            course_id=context.course_id,
            state=state,
            can_retake=state
            in {RetakeState.ELIGIBLE, RetakeState.ADDITIONAL_ATTEMPT_AVAILABLE}
            and context.can_retake,
            allowance=context.allowance,
            blockers=context.blockers,
            previous_attempt_id=(
                context.previous_attempt.attempt_id if context.previous_attempt else None
            ),
            previous_attempt_number=(
                context.previous_attempt.attempt_number if context.previous_attempt else None
            ),
            next_attempt_number=context.next_attempt_number,
            configuration_version_id=(
                context.target_configuration.configuration_version_id
                if context.target_configuration
                else None
            ),
            configuration_version_number=(
                context.target_configuration.version if context.target_configuration else None
            ),
            configuration_version_source=context.configuration_version_source,
            # Guidance appears exactly when the allowance is the problem, whatever the headline
            # state, so a learner blocked by both a spent allowance and a withdrawn quiz still
            # learns that an administrator could help with one of the two.
            guidance=self._settings.exhausted_contact_guidance if exhausted else None,
            anomalies=context.anomalies,
        )

    # ---------------------------------------------------- configuration

    async def _resolve_target_configuration(
        self,
        *,
        quiz_id: str,
        course_id: str,
        previous: AttemptContext | None,
        locked: QuizConfigurationVersion | None,
    ) -> tuple[
        QuizConfigurationVersion | None,
        ConfigurationVersionSource | None,
        RetakeBlocker | None,
    ]:
        """Decide which immutable version the retake locks. See the module docstring."""
        if self._settings.retake_configuration_policy == CARRY_FORWARD_POLICY:
            if locked is None:
                return None, None, None  # a CONFIGURATION_UNAVAILABLE blocker is already present
            return locked, ConfigurationVersionSource.PINNED_TO_PREVIOUS, None

        active = await self._configurations.get_active_configuration(quiz_id)
        if active is None:
            return (
                None,
                None,
                blocker(
                    RetakeBlockerCode.CONFIGURATION_UNAVAILABLE,
                    "The quiz has no active configuration version, so no new attempt can be "
                    "created.",
                    quiz_id=quiz_id,
                ),
            )

        if active.quiz_id != quiz_id or (course_id and active.course_id != course_id):
            # The guard that makes an accidental switch impossible rather than unlikely.
            logger.error(
                "retake.configuration_scope_mismatch",
                extra={
                    "quiz_id": quiz_id,
                    "course_id": course_id,
                    "configuration_version_id": active.configuration_version_id,
                },
            )
            return (
                None,
                None,
                blocker(
                    RetakeBlockerCode.CONFIGURATION_UNAVAILABLE,
                    "The active configuration version does not belong to this quiz and course.",
                    configuration_version_id=active.configuration_version_id,
                ),
            )

        if locked is not None and (
            active.configuration_version_id == locked.configuration_version_id
        ):
            return active, ConfigurationVersionSource.CARRIED_FORWARD, None
        if locked is None:
            # No previous attempt to compare with; the retake simply locks what is active.
            return active, ConfigurationVersionSource.CARRIED_FORWARD, None
        return active, ConfigurationVersionSource.ADVANCED_TO_ACTIVE, None


# ---------------------------------------------------------------------------
# Attempt-history helpers
# ---------------------------------------------------------------------------


def _latest_retakeable(attempts: tuple[AttemptContext, ...]) -> AttemptContext | None:
    """The most recent submitted attempt — the one a retake follows."""
    submitted = [attempt for attempt in attempts if attempt.retakeable]
    if not submitted:
        return None
    return max(submitted, key=lambda attempt: attempt.attempt_number)


def _first_open(attempts: tuple[AttemptContext, ...]) -> AttemptContext | None:
    return next((attempt for attempt in attempts if attempt.open), None)


def _in_flight(requests: tuple[RetakeRequest, ...]) -> RetakeRequest | None:
    """A reservation that has not yet become an attempt.

    Reported as a blocker rather than silently ignored: a learner whose retake is mid-creation
    should be told to wait, not handed a second one.
    """
    return next((request for request in requests if request.reserved), None)
