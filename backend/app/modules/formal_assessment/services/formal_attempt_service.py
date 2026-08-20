"""Running a formal attempt: start, autosave, submit, disconnect, and the refusals (§3, §4, §5, §6).

The ordered sequence a start goes through, and why the order is what it is::

    1  load the learner's open formal record            (reads only)
    2  the conditions gate                              (§1)
    3  the identity gate                                (§2)
    4  refuse a formal attempt already under way        (§3)
    5  refuse when UC-03 already has an open attempt    (its rule, checked before we write)
    ────────────────────────────────────────────────────────────  point of no return
    6  claim the device session                          (the concurrency guard, §3, §20)
    7  ask UC-03 to deliver the attempt
    8  on failure: release the session, report retryable
    9  on success: ACTIVE, audit, hand back the session token once

**Step 6 before step 7 is the whole design of the start path.** The session claim is a uniqueness
constraint, so of two simultaneous starts exactly one gets past it — and it gets past it *before*
any attempt is created. A second device therefore cannot cause a second UC-03 attempt to exist; it
is turned away while the only thing written is its own rejection record.

WHAT PAUSE AND RESUME DO HERE
-----------------------------
They refuse, record and audit. There is no pause state to move into and no resume operation on a
formal attempt — a learner continues in the session they already hold, and a session that ended
cannot be reopened. The endpoints exist because §4 requires the backend to *reject* these operations
rather than merely not offer them: a client that calls them gets a stable error code, and the
attempt gets an anomaly flag showing it was tried.

WHAT AUTOSAVE DOES HERE
-----------------------
Almost nothing, deliberately (§6). UC-09 checks the state and the session and hands the answers to
UC-03's existing autosave unread. There is no second autosave, no second validator and no second
copy of the learner's answers.

WHY THE DISCONNECT PATH IS IDEMPOTENT
-------------------------------------
``handle_disconnect`` claims the auto-submission with a compare-and-set into
AUTO_SUBMIT_IN_PROGRESS. Only one caller can win that write. Every later disconnect event — a second
beacon from the browser, the session monitor, an operator — finds the attempt already claimed or
already submitted and returns the existing outcome instead of submitting again. And because
AUTO_SUBMIT_IN_PROGRESS has exactly one exit, a disconnected attempt can never return to ACTIVE.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.formal_assessment.domain.anomalies import anomaly
from app.modules.formal_assessment.domain.attempt import DisconnectRecord, FormalAttempt
from app.modules.formal_assessment.domain.device import DeviceDescriptor, DeviceSession
from app.modules.formal_assessment.domain.enums import (
    UC03_STATUS_FOR_FORMAL_STATE,
    FormalAnomalyCode,
    FormalAttemptState,
    FormalAuditEvent,
    FormalSubmissionReason,
)
from app.modules.formal_assessment.domain.errors import (
    ConcurrentModificationError,
    DisconnectSubmissionConflictError,
    FormalAttemptAlreadyStartedError,
    FormalAttemptAlreadySubmittedError,
    FormalAttemptNotActiveError,
    FormalAttemptNotFoundError,
    PauseNotAllowedError,
    ResumeNotAllowedError,
)
from app.modules.formal_assessment.domain.idempotency import submission_key
from app.modules.formal_assessment.integration.audit import FormalAuditLog, safe_record
from app.modules.formal_assessment.integration.uc03 import (
    AnswerSubmission,
    AttemptProvider,
    AutosavedState,
    AutosaveResult,
    CreateAttemptRequest,
    SubmissionRequest,
    SubmittedState,
)
from app.modules.formal_assessment.repositories.protocols import FormalAttemptRepository
from app.modules.formal_assessment.services.conditions_service import FormalConditionsService
from app.modules.formal_assessment.services.device_session_service import DeviceSessionService
from app.modules.formal_assessment.services.identity_service import FormalIdentityService
from app.modules.formal_assessment.services.policy_service import FormalPolicyService
from app.modules.formal_assessment.services.result_service import FormalResultService

logger = get_logger(__name__)

#: How many answers one autosave call may carry. UC-03 applies its own limit; this one stops an
#: oversized body reaching it at all.
MAX_AUTOSAVE_ANSWERS = 500


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """A started formal attempt, its session and the upstream attempt."""

    formal_attempt: FormalAttempt
    session: DeviceSession
    #: True when a retry found the attempt it had already started.
    replayed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.formal_attempt.as_dict(),
            # The token is returned here and nowhere else: this is the response the registering
            # device
            # receives, and a replayed registration is the same device asking again.
            "session": self.session.as_dict(include_token=True),
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """A submitted formal attempt. ``replayed`` distinguishes a duplicate submit from a new one."""

    formal_attempt: FormalAttempt
    submitted_state: SubmittedState | None = None
    replayed: bool = False
    auto_submitted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.formal_attempt.as_dict(),
            "submitted_state": self.submitted_state.as_dict() if self.submitted_state else None,
            "replayed": self.replayed,
            "auto_submitted": self.auto_submitted,
        }


@dataclass(frozen=True, slots=True)
class FormalAttemptStatus:
    """Everything a client needs to render the state of a formal attempt without deciding anything.
    """

    formal_attempt: FormalAttempt
    upstream_attempt: dict[str, Any] | None = None
    autosaved_state: AutosavedState | None = None
    session_state: str | None = None
    #: The refusals a client should not attempt, stated by the backend rather than inferred.
    pause_allowed: bool = False
    resume_allowed: bool = False
    ai_coaching_allowed: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.formal_attempt.as_dict(),
            "upstream_attempt": self.upstream_attempt,
            "autosaved_state": self.autosaved_state.as_dict() if self.autosaved_state else None,
            "session_state": self.session_state,
            "pause_allowed": self.pause_allowed,
            "resume_allowed": self.resume_allowed,
            "ai_coaching_allowed": self.ai_coaching_allowed,
        }


class FormalAttemptService:
    def __init__(
        self,
        *,
        attempts: FormalAttemptRepository,
        upstream: AttemptProvider,
        policies: FormalPolicyService,
        conditions: FormalConditionsService,
        identity: FormalIdentityService,
        sessions: DeviceSessionService,
        results: FormalResultService,
        audit: FormalAuditLog,
        clock: Clock,
    ) -> None:
        self._attempts = attempts
        self._upstream = upstream
        self._policies = policies
        self._conditions = conditions
        self._identity = identity
        self._sessions = sessions
        self._results = results
        self._audit = audit
        self._clock = clock

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_owned(self, learner_id: str, formal_attempt_id: str) -> FormalAttempt:
        """Load a formal attempt, scoped to its owner (§17, §19).

        The ownership check is a repository query filtered on the learner, so a guessed id is a 404
        rather than someone else's record. The distinct ``FormalAttemptOwnershipError`` exists for
        the paths that have already established the record exists.
        """
        record = await self._attempts.get_for_learner(learner_id, formal_attempt_id)
        if record is None:
            raise FormalAttemptNotFoundError(formal_attempt_id)
        return record

    async def find_open(self, learner_id: str, quiz_id: str) -> FormalAttempt | None:
        return await self._attempts.find_open_for_quiz(learner_id, quiz_id)

    async def list_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        return await self._attempts.list_for_learner(learner_id)

    async def status(self, learner_id: str, formal_attempt_id: str) -> FormalAttemptStatus:
        """The authoritative status of a formal attempt (§17).

        Two things happen here besides reading. UC-03's own status is compared with this record's
        state and a mismatch is flagged rather than papered over. And a SUBMITTED attempt whose
        result has not yet been resolved triggers a resolution attempt — scoring is asynchronous, so
        the honest moment to look is whenever somebody asks.
        """
        record = await self.get_owned(learner_id, formal_attempt_id)

        if record.state is FormalAttemptState.SUBMITTED:
            resolution = await self._results.resolve(record)
            record = resolution.formal_attempt

        upstream = None
        autosaved = None
        if record.attempt_id:
            upstream_attempt = await self._upstream.get_attempt(record.attempt_id)
            if upstream_attempt is not None:
                upstream = upstream_attempt.as_dict()
                record = await self._reconcile(record, upstream_attempt.status)
            if record.in_progress:
                autosaved = await self._upstream.get_latest_autosaved_state(record.attempt_id)

        active_session = None
        if record.in_progress:
            active_session = await self._sessions_active(record)

        return FormalAttemptStatus(
            formal_attempt=record,
            upstream_attempt=upstream,
            autosaved_state=autosaved,
            session_state=active_session.state.value if active_session else None,
            # Stated rather than implied: a client asks the backend what is permitted, and the
            # answer for
            # a formal attempt is always the same two noes.
            pause_allowed=False,
            resume_allowed=False,
            ai_coaching_allowed=not record.in_progress,
        )

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start(
        self,
        *,
        learner_id: str,
        quiz_id: str,
        device: DeviceDescriptor | None = None,
        client_request_id: str | None = None,
        retake_of_attempt_id: str | None = None,
    ) -> StartOutcome:
        """Start the formal attempt (§1, §2, §3)."""
        policy = await self._policies.require_available(quiz_id)

        record = await self._attempts.find_open_for_quiz(learner_id, quiz_id)
        if record is None:
            # Nothing acknowledged for this sitting, so the conditions gate is the refusal to report
            # —
            # raised by the conditions service so there is one wording and one code for it.
            self._conditions.raise_not_acknowledged(learner_id=learner_id, quiz_id=quiz_id)

        if record.state in (FormalAttemptState.ACTIVE, FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS):
            # Already under way. A retry carrying the original client request id replays its
            # session;
            # anything else is a second device and is refused by the session service.
            registration = await self._sessions.register(
                record, device=device, client_request_id=client_request_id
            )
            return StartOutcome(
                formal_attempt=record, session=registration.session, replayed=True
            )

        self._conditions.require_acknowledged(record)
        self._identity.require_confirmed(record)

        open_upstream = await self._upstream.find_open_attempt(learner_id, quiz_id)
        if open_upstream is not None:
            # UC-03 permits one open attempt per quiz. Reported before anything is written, and as a
            # formal refusal rather than as an upstream constraint violation.
            raise FormalAttemptAlreadyStartedError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )

        # The session claim comes first: it is the uniqueness constraint that decides which of two
        # concurrent starts proceeds, and it must decide that before an attempt is created.
        registration = await self._sessions.register(
            record, device=device, client_request_id=client_request_id
        )
        session = registration.session

        try:
            upstream_attempt = await self._upstream.create_attempt(
                CreateAttemptRequest(
                    learner_id=learner_id,
                    course_id=policy.course_id,
                    quiz_id=quiz_id,
                    formal_assessment=True,
                    retake_of_attempt_id=retake_of_attempt_id or record.retake_of_attempt_id,
                    idempotency_key=record.idempotency_key,
                )
            )
        except Exception:
            # Nothing was delivered, so the lock must not be left held: releasing it lets the
            # learner
            # try again from the same or another device.
            await self._sessions.close(record, reason="ATTEMPT_CREATION_FAILED")
            raise

        now = to_iso(self._clock.now())
        try:
            started = await self._attempts.save(
                record.start(
                    attempt_id=upstream_attempt.attempt_id,
                    session_id=session.session_id,
                    now=now,
                    attempt_number=upstream_attempt.attempt_number,
                    configuration_version_id=upstream_attempt.configuration_version_id,
                    retake_of_attempt_id=retake_of_attempt_id,
                )
            )
        except ConcurrentModificationError:
            # Somebody else wrote to the record between the read and this save. The session claim
            # means
            # they cannot have started the attempt, so re-read and apply the start onto the winner.
            fresh = await self._attempts.get(record.formal_attempt_id)
            if fresh is None:  # pragma: no cover - there is no delete
                raise
            if fresh.state is FormalAttemptState.ACTIVE:
                return StartOutcome(formal_attempt=fresh, session=session, replayed=True)
            started = await self._attempts.save(
                fresh.start(
                    attempt_id=upstream_attempt.attempt_id,
                    session_id=session.session_id,
                    now=now,
                    attempt_number=upstream_attempt.attempt_number,
                    configuration_version_id=upstream_attempt.configuration_version_id,
                    retake_of_attempt_id=retake_of_attempt_id,
                )
            )

        await safe_record(
            self._audit,
            FormalAuditEvent.FORMAL_ATTEMPT_STARTED,
            formal_attempt_id=started.formal_attempt_id,
            learner_id=learner_id,
            course_id=policy.course_id,
            quiz_id=quiz_id,
            attempt_id=started.attempt_id,
            attempt_number=started.attempt_number,
            session_id=session.session_id,
            conditions_version=started.acknowledged_version(),
            configuration_version_id=started.configuration_version_id,
        )
        return StartOutcome(formal_attempt=started, session=session, replayed=False)

    # ------------------------------------------------------------------
    # Refusals (§4)
    # ------------------------------------------------------------------

    async def reject_pause(self, learner_id: str, formal_attempt_id: str) -> None:
        """Refuse a pause, record it, audit it (§4). There is no code path that pauses."""
        record = await self.get_owned(learner_id, formal_attempt_id)
        now = to_iso(self._clock.now())
        await self._note_anomaly(
            record, FormalAnomalyCode.PAUSE_OR_RESUME_ATTEMPTED, now=now, operation="PAUSE"
        )
        await safe_record(
            self._audit,
            FormalAuditEvent.PAUSE_REJECTED,
            formal_attempt_id=record.formal_attempt_id,
            learner_id=learner_id,
            quiz_id=record.quiz_id,
            attempt_id=record.attempt_id,
            state=record.state.value,
        )
        raise PauseNotAllowedError(
            formal_attempt_id=record.formal_attempt_id, state=record.state.value
        )

    async def reject_resume(self, learner_id: str, formal_attempt_id: str) -> None:
        """Refuse a resume, record it, audit it (§4, §5).

        Refused in every state, including ACTIVE. A learner who is still connected does not need to
        resume — they continue in the session they hold — and a learner who is not connected must
        not be able to come back. One rule, no state-dependent exceptions to reason about.
        """
        record = await self.get_owned(learner_id, formal_attempt_id)
        now = to_iso(self._clock.now())
        await self._note_anomaly(
            record, FormalAnomalyCode.PAUSE_OR_RESUME_ATTEMPTED, now=now, operation="RESUME"
        )
        await safe_record(
            self._audit,
            FormalAuditEvent.RESUME_REJECTED,
            formal_attempt_id=record.formal_attempt_id,
            learner_id=learner_id,
            quiz_id=record.quiz_id,
            attempt_id=record.attempt_id,
            state=record.state.value,
        )
        raise ResumeNotAllowedError(
            formal_attempt_id=record.formal_attempt_id, state=record.state.value
        )

    # ------------------------------------------------------------------
    # Autosave (§6)
    # ------------------------------------------------------------------

    async def autosave(
        self,
        *,
        learner_id: str,
        formal_attempt_id: str,
        session_token: str | None,
        answers: tuple[AnswerSubmission, ...],
    ) -> AutosaveResult:
        """Pass an autosave through to UC-03 after the formal checks (§6).

        The checks are the value this method adds: the attempt is being sat, the caller holds the
        authoritative session, and the record owns the upstream attempt. The answers themselves are
        handed over unread.
        """
        record = await self.get_owned(learner_id, formal_attempt_id)
        if record.submitted:
            raise FormalAttemptAlreadySubmittedError(
                formal_attempt_id=record.formal_attempt_id, submitted_at=record.submitted_at
            )
        if not record.in_progress or record.attempt_id is None:
            raise FormalAttemptNotActiveError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )
        if record.state is FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS:
            # The disconnect has already claimed the submission. Accepting answers now would change
            # what
            # is being submitted underneath the auto-submission.
            raise FormalAttemptNotActiveError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )
        if not answers:
            raise FormalAttemptNotActiveError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )

        session = await self._sessions.authorise(record, session_token=session_token)
        result = await self._upstream.save_answers(
            record.attempt_id, answers[:MAX_AUTOSAVE_ANSWERS]
        )
        await self._sessions.heartbeat(session)
        return result

    async def heartbeat(
        self, *, learner_id: str, formal_attempt_id: str, session_token: str | None
    ) -> DeviceSession:
        """Confirm the session is still the authoritative one, and record that it is alive (§3)."""
        record = await self.get_owned(learner_id, formal_attempt_id)
        if not record.in_progress:
            raise FormalAttemptNotActiveError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )
        session = await self._sessions.authorise(record, session_token=session_token)
        return await self._sessions.heartbeat(session)

    # ------------------------------------------------------------------
    # Submission (§5, §20)
    # ------------------------------------------------------------------

    async def submit(
        self, *, learner_id: str, formal_attempt_id: str, session_token: str | None
    ) -> SubmissionOutcome:
        """Submit the formal attempt at the learner's request (§20).

        A duplicate submit is a **replay**, not an error: the second request returns the submission
        the first one made. That is what "two submit requests should not create two submissions"
        means from
        the caller's side, and it is why the state machine has no SUBMITTED -> SUBMITTED edge to
        abuse.
        """
        record = await self.get_owned(learner_id, formal_attempt_id)

        if record.submitted:
            return SubmissionOutcome(
                formal_attempt=record,
                replayed=True,
                auto_submitted=record.auto_submitted,
                submitted_state=None,
            )
        if record.state is FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS:
            # A disconnect claimed the submission first. Its answers are the same autosaved state
            # this
            # request would have submitted, so nothing is lost — but two submissions must not be
            # made.
            raise DisconnectSubmissionConflictError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )
        if not record.in_progress or record.attempt_id is None:
            raise FormalAttemptNotActiveError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )

        await self._sessions.authorise(record, session_token=session_token)
        submitted = await self._commit(
            record, reason=FormalSubmissionReason.LEARNER_CONFIRMED, disconnect=None
        )
        return submitted

    async def handle_disconnect(
        self,
        *,
        formal_attempt: FormalAttempt,
        reported_by: str,
        last_seen_at: str | None = None,
        reason: str | None = None,
    ) -> SubmissionOutcome:
        """Handle a formal-session disconnect (§5).

        The sequence the specification asks for, in order: identify the authoritative attempt, take
        the latest valid autosaved state, submit it, record the reason, mark the attempt, prevent
        any resume, and audit it. Idempotent at every step — a second event returns the first one's
        outcome.
        """
        record = formal_attempt

        if record.submitted:
            return SubmissionOutcome(
                formal_attempt=record, replayed=True, auto_submitted=record.auto_submitted
            )
        if not record.in_progress or record.attempt_id is None:
            raise FormalAttemptNotActiveError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )

        now = to_iso(self._clock.now())
        autosaved = await self._upstream.get_latest_autosaved_state(record.attempt_id)
        disconnect = DisconnectRecord(
            detected_at=now,
            reported_by=reported_by,
            last_seen_at=last_seen_at,
            autosaved_at=autosaved.saved_at if autosaved else None,
            answered_questions=autosaved.answered_questions if autosaved else None,
            total_questions=autosaved.total_questions if autosaved else None,
            reason=reason,
        )

        await safe_record(
            self._audit,
            FormalAuditEvent.DISCONNECT_DETECTED,
            formal_attempt_id=record.formal_attempt_id,
            learner_id=record.learner_id,
            quiz_id=record.quiz_id,
            attempt_id=record.attempt_id,
            reported_by=reported_by,
            last_seen_at=last_seen_at,
            autosaved_at=disconnect.autosaved_at,
            answered_questions=disconnect.answered_questions,
        )

        if record.state is FormalAttemptState.ACTIVE:
            try:
                record = await self._attempts.save(record.claim_auto_submit(disconnect, now=now))
            except ConcurrentModificationError:
                # Another disconnect event, or the learner's own submit, got there first. Read the
                # winner
                # and report *its* outcome: this is the duplicate-disconnect guarantee.
                fresh = await self._attempts.get(record.formal_attempt_id)
                if fresh is None:  # pragma: no cover - there is no delete
                    raise
                if fresh.submitted:
                    return SubmissionOutcome(
                        formal_attempt=fresh, replayed=True, auto_submitted=fresh.auto_submitted
                    )
                if fresh.state is not FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS:
                    raise DisconnectSubmissionConflictError(
                        formal_attempt_id=fresh.formal_attempt_id, state=fresh.state.value
                    ) from None
                record = fresh
        else:
            # Already AUTO_SUBMIT_IN_PROGRESS: a previous event claimed it and either is still
            # finishing or
            # failed part-way. Completing it is safe and idempotent, so carry on rather than
            # refusing.
            logger.info(
                "formal.disconnect.completing_existing_claim",
                extra={"formal_attempt_id": record.formal_attempt_id},
            )

        await safe_record(
            self._audit,
            FormalAuditEvent.AUTO_SUBMIT_STARTED,
            formal_attempt_id=record.formal_attempt_id,
            learner_id=record.learner_id,
            attempt_id=record.attempt_id,
            autosaved_at=disconnect.autosaved_at,
            answered_questions=disconnect.answered_questions,
        )

        await self._sessions.mark_disconnected(record, reason="DISCONNECT")

        outcome = await self._commit(
            record,
            reason=FormalSubmissionReason.DISCONNECT_AUTO_SUBMIT,
            disconnect=record.disconnect or disconnect,
            autosaved=autosaved,
        )

        await safe_record(
            self._audit,
            FormalAuditEvent.AUTO_SUBMIT_COMPLETED,
            formal_attempt_id=outcome.formal_attempt.formal_attempt_id,
            learner_id=outcome.formal_attempt.learner_id,
            attempt_id=outcome.formal_attempt.attempt_id,
            submitted_at=outcome.formal_attempt.submitted_at,
            replayed=outcome.replayed,
        )
        return outcome

    async def handle_disconnect_for_learner(
        self,
        *,
        learner_id: str,
        formal_attempt_id: str,
        reported_by: str,
        last_seen_at: str | None = None,
        reason: str | None = None,
    ) -> SubmissionOutcome:
        """Ownership-scoped disconnect, for a learner's own client reporting it."""
        record = await self.get_owned(learner_id, formal_attempt_id)
        return await self.handle_disconnect(
            formal_attempt=record,
            reported_by=reported_by,
            last_seen_at=last_seen_at,
            reason=reason,
        )

    async def handle_disconnect_by_id(
        self,
        *,
        formal_attempt_id: str,
        reported_by: str,
        last_seen_at: str | None = None,
        reason: str | None = None,
    ) -> SubmissionOutcome:
        """Disconnect reported by the platform's session monitor, which has no learner identity."""
        record = await self._attempts.get(formal_attempt_id)
        if record is None:
            raise FormalAttemptNotFoundError(formal_attempt_id)
        return await self.handle_disconnect(
            formal_attempt=record,
            reported_by=reported_by,
            last_seen_at=last_seen_at,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _commit(
        self,
        record: FormalAttempt,
        *,
        reason: FormalSubmissionReason,
        disconnect: DisconnectRecord | None,
        autosaved: AutosavedState | None = None,
    ) -> SubmissionOutcome:
        """Submit through UC-03 and record it. The one place a formal attempt becomes submitted."""
        if record.attempt_id is None:  # pragma: no cover - callers check first
            raise FormalAttemptNotActiveError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )

        submitted_state = await self._upstream.submit_attempt(
            SubmissionRequest(
                attempt_id=record.attempt_id,
                reason=reason,
                idempotency_key=submission_key(record.attempt_id),
                disconnect_detected_at=disconnect.detected_at if disconnect else None,
            )
        )

        now = to_iso(self._clock.now())
        try:
            stored = await self._attempts.save(
                record.submit(
                    reason=reason,
                    now=now,
                    submitted_at=submitted_state.submitted_at,
                    disconnect=disconnect,
                )
            )
        except ConcurrentModificationError:
            fresh = await self._attempts.get(record.formal_attempt_id)
            if fresh is None:  # pragma: no cover - there is no delete
                raise
            if fresh.submitted:
                # Somebody else completed the same submission. UC-03's submit is idempotent, so
                # there is
                # exactly one upstream submission; this call simply reports it.
                return SubmissionOutcome(
                    formal_attempt=fresh,
                    submitted_state=submitted_state,
                    replayed=True,
                    auto_submitted=fresh.auto_submitted,
                )
            stored = await self._attempts.save(
                fresh.submit(
                    reason=reason,
                    now=now,
                    submitted_at=submitted_state.submitted_at,
                    disconnect=disconnect,
                )
            )

        stored = await self._flag_submission_anomalies(stored, autosaved=autosaved, now=now)

        if reason is FormalSubmissionReason.LEARNER_CONFIRMED:
            await self._sessions.close(stored, reason="SUBMITTED")

        await safe_record(
            self._audit,
            FormalAuditEvent.FORMAL_ATTEMPT_SUBMITTED,
            formal_attempt_id=stored.formal_attempt_id,
            learner_id=stored.learner_id,
            quiz_id=stored.quiz_id,
            attempt_id=stored.attempt_id,
            submission_reason=reason.value,
            submitted_at=stored.submitted_at,
            already_submitted_upstream=submitted_state.already_submitted,
        )

        # Resolving the result is a separate concern and a separate failure: an unreachable scoring
        # module must not turn a successful submission into an error, so this is best effort and the
        # status endpoint retries it.
        resolution = await self._results.try_resolve(stored)
        return SubmissionOutcome(
            formal_attempt=resolution,
            submitted_state=submitted_state,
            replayed=(
                submitted_state.already_submitted
                and reason is FormalSubmissionReason.LEARNER_CONFIRMED
            ),
            auto_submitted=reason is FormalSubmissionReason.DISCONNECT_AUTO_SUBMIT,
        )

    async def _flag_submission_anomalies(
        self, record: FormalAttempt, *, autosaved: AutosavedState | None, now: str
    ) -> FormalAttempt:
        """Record what an assessor should know about how this submission happened (§10).

        All of the observations are written as one change, so the compare-and-set sees a record
        exactly one version ahead of what is stored.
        """
        if not record.auto_submitted:
            return record

        observations = [
            anomaly(
                FormalAnomalyCode.AUTO_SUBMITTED_AFTER_DISCONNECT,
                observed_at=now,
                detected_at=record.disconnect.detected_at if record.disconnect else None,
                reported_by=record.disconnect.reported_by if record.disconnect else None,
            )
        ]
        if autosaved is None or not autosaved.exists:
            observations.append(
                anomaly(FormalAnomalyCode.NO_AUTOSAVED_STATE_AT_DISCONNECT, observed_at=now)
            )
        elif not autosaved.complete:
            observations.append(
                anomaly(
                    FormalAnomalyCode.AUTOSAVE_STATE_INCOMPLETE,
                    observed_at=now,
                    answered_questions=autosaved.answered_questions,
                    total_questions=autosaved.total_questions,
                )
            )

        try:
            return await self._attempts.save(record.with_anomalies(observations, now=now))
        except ConcurrentModificationError:
            logger.warning(
                "formal.anomaly_not_recorded",
                extra={"formal_attempt_id": record.formal_attempt_id},
            )
            return record

    async def _reconcile(self, record: FormalAttempt, upstream_status: str) -> FormalAttempt:
        """Flag a formal record and an upstream attempt that disagree.

        UC-09 does not "fix" either side: the attempt belongs to UC-03 and the supervision record
        belongs here, so a mismatch is an observation for a human, not something to overwrite. It is
        exactly the kind of thing that should reach an assessor rather than being resolved silently
        by whichever module noticed.
        """
        expected = UC03_STATUS_FOR_FORMAL_STATE.get(record.state)
        if expected is None or expected == upstream_status:
            return record
        # SUBMISSION_PENDING is UC-03's transient state on the way to SUBMITTED: not a disagreement.
        if upstream_status == "SUBMISSION_PENDING" and expected in {"ACTIVE", "SUBMITTED"}:
            return record

        now = to_iso(self._clock.now())
        try:
            return await self._attempts.save(
                record.with_anomaly(
                    anomaly(
                        FormalAnomalyCode.UPSTREAM_STATE_MISMATCH,
                        observed_at=now,
                        formal_state=record.state.value,
                        upstream_status=upstream_status,
                    ),
                    now=now,
                )
            )
        except ConcurrentModificationError:  # pragma: no cover - best effort
            return record

    async def _note_anomaly(
        self, record: FormalAttempt, code: FormalAnomalyCode, *, now: str, **details: Any
    ) -> None:
        try:
            fresh = await self._attempts.get(record.formal_attempt_id)
            if fresh is None:  # pragma: no cover - there is no delete
                return
            await self._attempts.save(
                fresh.with_anomaly(anomaly(code, observed_at=now, **details), now=now)
            )
        except Exception:  # noqa: BLE001 - an anomaly flag must never replace the refusal
            logger.warning(
                "formal.anomaly_not_recorded",
                extra={"formal_attempt_id": record.formal_attempt_id, "code": code.value},
            )

    async def _sessions_active(self, record: FormalAttempt) -> DeviceSession | None:
        sessions = await self._sessions.list_for_attempt(record)
        return next((session for session in sessions if session.active), None)
