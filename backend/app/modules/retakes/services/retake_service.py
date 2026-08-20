"""Creating a retake — the orchestration (§3, §4, §13, §14, §15, §16, §17).

Every rule in this module meets here, in one deliberately ordered sequence::

    1  resolve and validate the attempt being retaken   (reads only)
    2  derive the idempotency key from domain identity   (no client token)
    3  has this retake already happened?                 → replay, or refuse, or retry
    4  load the eligibility context                      (reads only)
    5  refuse if anything blocks it                      → nothing has been written yet
    6  resolve the configuration version and plan the questions
    ─────────────────────────────────────────────────────────────  point of no return
    7  reserve the attempt slot                          (the concurrency guard)
    8  ask UC-03 to create the attempt
    9  on failure: release the slot, report a retryable error
    10 on success: check the paper, record anomalies, complete the record

Steps 1–6 are reads and pure decisions. Nothing is persisted until step 7, so every refusal in
this service leaves the learner's history exactly as it was.

THE THREE PROPERTIES WORTH READING THE CODE FOR
-----------------------------------------------
**A retake cannot exceed the allowance, however the requests interleave (§15).** The reservation
at step 7 takes ``(learner_id, quiz_id, attempt_number)`` under a database uniqueness constraint,
and a RESERVED reservation counts as an attempt used. Two simultaneous requests compute the same
next attempt number; one insert wins and the other is refused. The check is not "read then write"
— the write *is* the check.

**A retried request cannot create a second attempt (§16).** The idempotency key is derived from
the previous attempt id, so a client that retries after a timeout produces the same key, finds the
completed record and receives the attempt that already exists.

**A failed retake does not cost the learner an attempt (§14).** Step 9 marks the reservation
FAILED, which releases the slot, and the same request can be sent again. The failure is recorded
on the retake, so a support question about a lost attempt has an answer.

WHAT THIS SERVICE NEVER DOES
----------------------------
It does not write to an attempt, an answer, a score, a pass/fail result, a feedback report or a
coaching session — there is no port here that could. §3's independence is structural: the previous
attempt is read to find out which questions to avoid, and that is the only way it is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.core.errors import AppError, PersistenceFailedError
from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.retakes.domain.anomalies import RetakeAnomaly, anomaly
from app.modules.retakes.domain.difference import QuestionSetDifference, compare_question_sets
from app.modules.retakes.domain.eligibility import RetakeEligibility
from app.modules.retakes.domain.enums import (
    RetakeAnomalyCode,
    RetakeBlockerCode,
    RetakeRequestStatus,
)
from app.modules.retakes.domain.errors import (
    AttemptCreationFailedError,
    AttemptInProgressError,
    AttemptNotFoundError,
    AttemptOwnershipError,
    AttemptSlotTakenError,
    ConfigurationUnavailableError,
    DuplicateRetakeRequestError,
    NoAttemptsRemainingError,
    NoCompletedAttemptError,
    PreviousAttemptNotRetakeableError,
    PreviousAttemptQuizMismatchError,
    PreviousAttemptSupersededError,
    QuizNotAvailableError,
    RetakeInProgressError,
    RetakeNotFoundError,
)
from app.modules.retakes.domain.idempotency import retake_key
from app.modules.retakes.domain.question_plan import RetakeQuestionPlan
from app.modules.retakes.domain.requests import RetakeRequest
from app.modules.retakes.ids import IdGenerator
from app.modules.retakes.integration.audit import RetakeAuditLog
from app.modules.retakes.integration.uc03 import (
    AttemptContext,
    AttemptProvider,
    DeliveredAttempt,
    RetakeAttemptRequest,
)
from app.modules.retakes.repositories.protocols import RetakeRequestRepository
from app.modules.retakes.services.eligibility_service import (
    RetakeContext,
    RetakeEligibilityService,
)
from app.modules.retakes.services.question_plan_service import (
    PlannedRetake,
    RetakeQuestionPlanService,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetakeOutcome:
    """What a retake request produced.

    ``replayed`` is True when the request was a repeat and the attempt already existed. The caller
    turns it into ``200`` rather than ``201``, so a client can tell "your retake was created" from
    "your retake already existed" without either being an error.
    """

    retake: RetakeRequest
    eligibility: RetakeEligibility
    attempt: DeliveredAttempt | None = None
    plan: RetakeQuestionPlan | None = None
    difference: QuestionSetDifference | None = None
    replayed: bool = False


#: Which blocker is reported when several apply. Ordered by what the caller can act on: a
#: withdrawn quiz or an attempt still open is actionable now; a spent allowance is last because it
#: is the one that needs an administrator.
_BLOCKER_PRECEDENCE: tuple[RetakeBlockerCode, ...] = (
    RetakeBlockerCode.QUIZ_NOT_AVAILABLE,
    RetakeBlockerCode.NO_COMPLETED_ATTEMPT,
    RetakeBlockerCode.PREVIOUS_ATTEMPT_NOT_COMPLETE,
    RetakeBlockerCode.ATTEMPT_IN_PROGRESS,
    RetakeBlockerCode.RETAKE_IN_PROGRESS,
    RetakeBlockerCode.CONFIGURATION_UNAVAILABLE,
    RetakeBlockerCode.NO_ATTEMPTS_REMAINING,
)


class RetakeService:
    def __init__(
        self,
        *,
        attempts: AttemptProvider,
        retakes: RetakeRequestRepository,
        eligibility: RetakeEligibilityService,
        plans: RetakeQuestionPlanService,
        audit: RetakeAuditLog,
        clock: Clock,
        new_id: IdGenerator,
        guidance: str,
    ) -> None:
        self._attempts = attempts
        self._retakes = retakes
        self._eligibility = eligibility
        self._plans = plans
        self._audit = audit
        self._clock = clock
        self._new_id = new_id
        self._guidance = guidance

    # ------------------------------------------------------------- reading

    async def get(self, learner_id: str, retake_id: str) -> RetakeRequest:
        """One retake, scoped to its owner. A guessed id is a 404, not someone else's record."""
        stored = await self._retakes.get_for_learner(learner_id, retake_id)
        if stored is None:
            raise RetakeNotFoundError(retake_id)
        return stored

    async def list_for_quiz(self, learner_id: str, quiz_id: str) -> tuple[RetakeRequest, ...]:
        return await self._retakes.list_for_learner_quiz(learner_id, quiz_id)

    # ------------------------------------------------------------ creation

    async def create(
        self, *, learner_id: str, quiz_id: str, previous_attempt_id: str | None = None
    ) -> RetakeOutcome:
        """Create a retake. See the module docstring for the ordering and why it matters."""
        # ---- 1. The attempt being retaken. ------------------------------
        previous = await self._resolve_previous_attempt(
            learner_id=learner_id, quiz_id=quiz_id, previous_attempt_id=previous_attempt_id
        )

        # ---- 2. The key, derived from domain identity (§16). ------------
        key = retake_key(learner_id, quiz_id, previous.attempt_id)

        # ---- 3. Has this retake already happened? -----------------------
        existing = await self._retakes.get_by_idempotency_key(key)
        if existing is not None and existing.completed:
            return await self._replay(existing)
        if existing is not None and existing.reserved:
            # Another request holds the slot and is mid-creation. Refusing is the honest answer;
            # creating a second attempt to be helpful is the bug §15 warns about.
            raise RetakeInProgressError(existing.retake_id, previous.attempt_id)

        # ---- 4. Eligibility, from the same code path the read endpoint uses.
        context = await self._eligibility.load(learner_id, quiz_id)
        eligibility = self._eligibility.describe(context)

        # A superseded previous attempt is refused here rather than in step 1, because deciding it
        # needs the learner's full attempt list.
        latest = context.previous_attempt
        if latest is not None and latest.attempt_id != previous.attempt_id:
            raise PreviousAttemptSupersededError(previous.attempt_id, latest.attempt_id)

        # ---- 5. Refuse, before anything is written. ---------------------
        blockers = self._blocking(context, ignore_retake_in_progress=existing is not None)
        if blockers:
            self._raise_blocker(context, blockers)

        configuration = context.target_configuration
        if configuration is None:  # pragma: no cover - a blocker always accompanies this
            raise ConfigurationUnavailableError(quiz_id)

        # ---- 6. Plan the questions (§5, §6, §8). ------------------------
        planned = await self._plans.build(
            config=configuration,
            course_id=context.course_id,
            previous_attempt=previous,
            attempts=context.attempts,
        )

        # ---- 7. Reserve the slot. The concurrency guard (§15). ----------
        try:
            reservation = await self._reserve(
                existing=existing,
                context=context,
                previous=previous,
                key=key,
                plan=planned.plan,
            )
        except _Replay as signal:
            # A concurrent request finished the identical retake while this one was planning.
            return await self._replay(signal.request)

        # ---- 8/9/10. Deliver, verify, complete. -------------------------
        return await self._deliver(
            reservation=reservation,
            context=context,
            eligibility=eligibility,
            planned=planned,
        )

    # ----------------------------------------------------------- internals

    async def _resolve_previous_attempt(
        self, *, learner_id: str, quiz_id: str, previous_attempt_id: str | None
    ) -> AttemptContext:
        """Find the attempt the retake follows, and refuse every way it could be wrong."""
        if previous_attempt_id:
            attempt = await self._attempts.get_attempt(previous_attempt_id)
            if attempt is None:
                raise AttemptNotFoundError(previous_attempt_id)
            if attempt.learner_id != learner_id:
                # Ownership before anything else: a guessed id must not reveal that it exists by
                # producing a different error later on.
                raise AttemptOwnershipError(previous_attempt_id, learner_id)
            if attempt.quiz_id != quiz_id:
                raise PreviousAttemptQuizMismatchError(
                    previous_attempt_id, attempt.quiz_id, quiz_id
                )
            if not attempt.retakeable:
                raise PreviousAttemptNotRetakeableError(previous_attempt_id, str(attempt.status))
            return attempt

        # No attempt named: retake the learner's most recent submitted one.
        attempts = await self._attempts.list_attempts(learner_id, quiz_id)
        submitted = [attempt for attempt in attempts if attempt.retakeable]
        if not submitted:
            open_attempt = next((attempt for attempt in attempts if attempt.open), None)
            if open_attempt is not None:
                raise PreviousAttemptNotRetakeableError(
                    open_attempt.attempt_id, str(open_attempt.status)
                )
            raise NoCompletedAttemptError(learner_id, quiz_id)
        return max(submitted, key=lambda attempt: attempt.attempt_number)

    def _blocking(
        self, context: RetakeContext, *, ignore_retake_in_progress: bool
    ) -> tuple[RetakeBlockerCode, ...]:
        """The blocker codes that stop this particular request.

        ``ignore_retake_in_progress`` is set when the caller is retrying its *own* failed request:
        that request is the one in the store, so treating it as a competing retake would make a
        failed retake permanently unretryable.
        """
        codes = tuple(item.code for item in context.blockers)
        if ignore_retake_in_progress:
            codes = tuple(
                code for code in codes if code is not RetakeBlockerCode.RETAKE_IN_PROGRESS
            )
        return codes

    def _raise_blocker(
        self, context: RetakeContext, blockers: tuple[RetakeBlockerCode, ...]
    ) -> None:
        """Turn the highest-precedence blocker into the matching error."""
        present = set(blockers)
        code = next((item for item in _BLOCKER_PRECEDENCE if item in present), None)

        if code is RetakeBlockerCode.NO_ATTEMPTS_REMAINING:
            raise NoAttemptsRemainingError(
                maximum_attempts=context.allowance.maximum_attempts,
                attempts_used=context.allowance.attempts_used,
                granted_attempts=context.allowance.granted_attempts,
                guidance=self._guidance,
            )
        if code is RetakeBlockerCode.ATTEMPT_IN_PROGRESS:
            raise AttemptInProgressError(
                context.open_attempt.attempt_id if context.open_attempt else context.quiz_id
            )
        if code is RetakeBlockerCode.RETAKE_IN_PROGRESS:
            in_flight = context.in_flight_retake
            raise RetakeInProgressError(
                in_flight.retake_id if in_flight else "",
                in_flight.previous_attempt_id if in_flight else "",
            )
        if code is RetakeBlockerCode.QUIZ_NOT_AVAILABLE:
            reason = next(
                (
                    item.details.get("reason")
                    for item in context.blockers
                    if item.code is RetakeBlockerCode.QUIZ_NOT_AVAILABLE
                ),
                None,
            )
            raise QuizNotAvailableError(context.quiz_id, reason)
        if code is RetakeBlockerCode.CONFIGURATION_UNAVAILABLE:
            raise ConfigurationUnavailableError(context.quiz_id)
        # NO_COMPLETED_ATTEMPT and PREVIOUS_ATTEMPT_NOT_COMPLETE both mean the same thing to a
        # caller: there is nothing submitted to retake.
        raise NoCompletedAttemptError(context.learner_id, context.quiz_id)

    async def _reserve(
        self,
        *,
        existing: RetakeRequest | None,
        context: RetakeContext,
        previous: AttemptContext,
        key: str,
        plan: RetakeQuestionPlan,
    ) -> RetakeRequest:
        """Take the attempt slot, or retry a previously failed request into it."""
        now = to_iso(self._clock.now())
        configuration = context.target_configuration
        assert configuration is not None  # guaranteed by the caller
        attempt_number = context.next_attempt_number

        if existing is not None and existing.failed:
            # Retry of the caller's own failed request: same retake id and key, refreshed
            # decisions, and the slot re-acquired under the same constraint.
            candidate = replace(
                existing.reopened(at=now),
                attempt_number=attempt_number,
                configuration_version_id=configuration.configuration_version_id,
                configuration_version_number=configuration.version,
                configuration_version_source=context.configuration_version_source
                or existing.configuration_version_source,
                question_plan=plan.as_dict(),
            )
            return await self._retakes.save(candidate)

        request = RetakeRequest(
            retake_id=self._new_id(),
            idempotency_key=key,
            learner_id=context.learner_id,
            course_id=context.course_id,
            quiz_id=context.quiz_id,
            previous_attempt_id=previous.attempt_id,
            attempt_number=attempt_number,
            configuration_version_id=configuration.configuration_version_id,
            configuration_version_number=configuration.version,
            configuration_version_source=context.configuration_version_source,  # type: ignore[arg-type]
            status=RetakeRequestStatus.RESERVED,
            requested_at=now,
            updated_at=now,
            question_plan=plan.as_dict(),
        )

        try:
            return await self._retakes.reserve(request)
        except DuplicateRetakeRequestError:
            # A concurrent request inserted the same key between step 3 and here.
            winner = await self._retakes.get_by_idempotency_key(key)
            if winner is not None and winner.completed:
                raise _Replay(winner) from None
            raise RetakeInProgressError(
                winner.retake_id if winner else "", previous.attempt_id
            ) from None
        except AttemptSlotTakenError:
            # A different retake took this attempt number first. The allowance held.
            logger.warning(
                "retake.slot_conflict",
                extra={
                    "learner_id": context.learner_id,
                    "quiz_id": context.quiz_id,
                    "attempt_number": attempt_number,
                },
            )
            raise

    async def _deliver(
        self,
        *,
        reservation: RetakeRequest,
        context: RetakeContext,
        eligibility: RetakeEligibility,
        planned: PlannedRetake,
    ) -> RetakeOutcome:
        """Ask UC-03 for the attempt, then verify and record what came back."""
        configuration = context.target_configuration
        assert configuration is not None
        plan = planned.plan

        request = RetakeAttemptRequest(
            learner_id=context.learner_id,
            course_id=context.course_id,
            quiz_id=context.quiz_id,
            configuration_version_id=configuration.configuration_version_id,
            attempt_number=reservation.attempt_number,
            retake_of_attempt_id=reservation.previous_attempt_id,
            idempotency_key=reservation.idempotency_key,
            deprioritised_question_ids=plan.excluded_question_ids,
        )

        try:
            delivered = await self._attempts.create_retake_attempt(request)
        except AppError as exc:
            # A refusal UC-03 expressed in the shared taxonomy — including
            # ``ProviderUnavailableError`` — is forwarded unchanged, but not before the slot is
            # released so the learner has not spent an attempt on a failure.
            await self._release(reservation, code=exc.code, message=exc.message)
            raise
        except Exception as exc:
            await self._release(
                reservation,
                code="ATTEMPT_CREATION_FAILED",
                message="The attempt could not be created.",
            )
            logger.error(
                "retake.attempt_creation_failed",
                extra={"retake_id": reservation.retake_id, "quiz_id": context.quiz_id},
                exc_info=exc,
            )
            raise AttemptCreationFailedError(
                "The retake attempt could not be created. No attempt was used; "
                "the request can be safely retried.",
                retake_id=reservation.retake_id,
            ) from exc

        # ---- verify what UC-03 delivered --------------------------------
        # Compared against the very reads the plan was built from, so the check cannot disagree
        # with the instruction that produced the paper.
        difference = compare_question_sets(
            previous_question_ids=planned.previous_question_ids,
            retake_question_ids=delivered.delivered_question_ids,
            expected_fresh_questions=plan.expected_fresh_questions,
            historical_question_ids=planned.historical_question_ids,
        )

        anomalies = _anomalies_for(
            plan=plan,
            difference=difference,
            reservation=reservation,
            delivered=delivered,
        )

        completed = await self._retakes.save(
            reservation.completed_with(
                attempt_id=delivered.attempt_id,
                at=to_iso(self._clock.now()),
                question_set_difference=difference.as_dict(),
                anomalies=anomalies,
            )
        )

        await self._audit.record(
            "retake_created",
            retake_id=completed.retake_id,
            learner_id=completed.learner_id,
            course_id=completed.course_id,
            quiz_id=completed.quiz_id,
            previous_attempt_id=completed.previous_attempt_id,
            attempt_id=completed.attempt_id,
            attempt_number=completed.attempt_number,
            configuration_version_id=completed.configuration_version_id,
            configuration_version_source=completed.configuration_version_source.value,
            exclusion_scope=plan.exclusion_scope.value,
            new_question_count=difference.new_question_count,
            repeated_question_count=difference.repeated_question_count,
        )

        return RetakeOutcome(
            retake=completed,
            eligibility=eligibility,
            attempt=delivered,
            plan=plan,
            difference=difference,
        )

    async def _release(self, reservation: RetakeRequest, *, code: str, message: str) -> None:
        """Mark a reservation FAILED so the attempt slot is not silently consumed (§14).

        A failure to record the failure is logged and swallowed: the caller is already receiving an
        error, and replacing it with a persistence error would hide what actually went wrong. The
        reservation is left RESERVED in that case — it blocks further retakes until an operator
        intervenes, which is the safe direction to fail in.
        """
        try:
            await self._retakes.save(
                reservation.failed_with(code=code, message=message, at=to_iso(self._clock.now()))
            )
        except PersistenceFailedError:
            logger.error(
                "retake.reservation_release_failed",
                extra={"retake_id": reservation.retake_id, "failure_code": code},
            )

    async def _replay(self, existing: RetakeRequest) -> RetakeOutcome:
        """Return the attempt a repeated request already produced (§16)."""
        eligibility = await self._eligibility.check(existing.learner_id, existing.quiz_id)
        delivered: DeliveredAttempt | None = None
        if existing.attempt_id:
            attempt = await self._attempts.get_attempt(existing.attempt_id)
            if attempt is not None:
                delivered = DeliveredAttempt(
                    attempt_id=attempt.attempt_id,
                    learner_id=attempt.learner_id,
                    course_id=attempt.course_id,
                    quiz_id=attempt.quiz_id,
                    attempt_number=attempt.attempt_number,
                    status=attempt.status,
                    configuration_version_id=attempt.configuration_version_id,
                    configuration_version_number=attempt.configuration_version_number,
                    delivered_question_ids=await self._safe_delivered_ids(attempt.attempt_id),
                    started_at=attempt.started_at,
                )
        return RetakeOutcome(
            retake=existing, eligibility=eligibility, attempt=delivered, replayed=True
        )

    async def _safe_delivered_ids(self, attempt_id: str) -> tuple[str, ...]:
        """Delivered ids, or empty when they cannot be read.

        Used only for the difference check and the replay view, both of which are reporting. A
        history record that cannot be read degrades the report; it does not fail an attempt that
        already exists.
        """
        try:
            return await self._attempts.get_delivered_question_ids(attempt_id)
        except Exception:
            logger.warning("retake.delivered_ids_unreadable", extra={"attempt_id": attempt_id})
            return ()


class _Replay(Exception):
    """Internal signal: a concurrent request completed the same retake first."""

    def __init__(self, request: RetakeRequest) -> None:
        super().__init__(request.retake_id)
        self.request = request


def _anomalies_for(
    *,
    plan: RetakeQuestionPlan,
    difference: QuestionSetDifference,
    reservation: RetakeRequest,
    delivered: DeliveredAttempt,
) -> tuple[RetakeAnomaly, ...]:
    """What is worth recording about a retake that has already been created.

    None of these can undo the attempt, and none of them is a failure. They are the difference
    between "the paper repeated three questions" being discoverable and being invisible.
    """
    found: list[RetakeAnomaly] = []

    if plan.reuse_expected or difference.repeated_question_count:
        found.append(
            anomaly(
                RetakeAnomalyCode.QUESTION_REUSE_UNAVOIDABLE,
                "The question bank could not supply enough unused questions, so some questions "
                "the learner has already seen were delivered again.",
                exclusion_scope=plan.exclusion_scope.value,
                reuse_reason=plan.reuse_reason.value if plan.reuse_reason else None,
                required_count=plan.required_count,
                unused_pool_size=plan.unused_pool_size,
                repeated_question_count=difference.repeated_question_count,
            )
        )

    if not difference.satisfied:
        # Alternatives existed and were not used. Recorded, not raised: the attempt is real and
        # the learner can sit it (§7).
        found.append(
            anomaly(
                RetakeAnomalyCode.QUESTION_SET_NOT_MEANINGFULLY_DIFFERENT,
                "The retake's question set is not as different from the previous attempt as the "
                "question bank allowed.",
                expected_fresh_questions=difference.expected_fresh_questions,
                new_question_count=difference.new_question_count,
                identical_question_set=difference.identical_question_set,
            )
        )

    if delivered.configuration_version_id != reservation.configuration_version_id:
        found.append(
            anomaly(
                RetakeAnomalyCode.CONFIGURATION_VERSION_MISMATCH,
                "The attempt was created against a different configuration version than the "
                "retake resolved.",
                resolved=reservation.configuration_version_id,
                delivered=delivered.configuration_version_id,
            )
        )

    if delivered.attempt_number != reservation.attempt_number:
        found.append(
            anomaly(
                RetakeAnomalyCode.ATTEMPT_NUMBER_MISMATCH,
                "The attempt was created with a different attempt number than the reservation "
                "held.",
                reserved=reservation.attempt_number,
                delivered=delivered.attempt_number,
            )
        )

    return tuple(found)
