"""The formal attempt record — UC-09's aggregate (§15, §16).

One record per formal assessment sitting. It holds the supervision facts UC-09 owns and *references*
everything it does not:

===========================  ==========================================================
Owned here                    Referenced
===========================  ==========================================================
formal lifecycle state        ``attempt_id`` — the UC-03 attempt (answers, timing, paper)
conditions acknowledgement    ``result`` — UC-04's score and UC-05's pass/fail, copied in
identity confirmation         ``review_id`` — the UC-09 review record
device session reference      certificate reference from the certificate workflow
disconnect / submission facts anomaly flags
===========================  ==========================================================

**It is not a second attempt.** There is no answer, no question, no score calculation and no timer
on this record. The learner's answers live in UC-03 and nowhere else, which is what stops UC-09 from
becoming a competing attempt store that could disagree with the real one.

EVERY TRANSITION IS A METHOD, AND EVERY METHOD VALIDATES
--------------------------------------------------------
The record is a frozen dataclass and each lifecycle method returns a *new* record after checking the
move against ``domain.state_machine``. Nothing mutates a formal attempt in place, so an invalid
transition cannot leave a half-changed record behind, and a service that forgets to check the state
machine cannot: the check is inside the only operation that can change the state.

``version`` is the optimistic-concurrency token. Every method increments it, and every repository
write is a compare-and-set on the value the caller read. That is what makes the concurrency
requirements in §20 hold without a lock the company's database may not offer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from app.modules.formal_assessment.domain.anomalies import FormalAnomaly, record_anomaly
from app.modules.formal_assessment.domain.conditions import FormalConditionCode
from app.modules.formal_assessment.domain.enums import (
    CERTIFICATE_ALLOWED_STATES,
    IN_PROGRESS_FORMAL_STATES,
    OPEN_FORMAL_STATES,
    SUBMITTED_FORMAL_STATES,
    FormalAttemptState,
    FormalSubmissionReason,
)
from app.modules.formal_assessment.domain.errors import InvalidStateTransitionError
from app.modules.formal_assessment.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    can_transition,
)


@dataclass(frozen=True, slots=True)
class ConditionsAcknowledgement:
    """What the learner acknowledged, and which wording they acknowledged."""

    conditions_version: str
    acknowledged_codes: tuple[FormalConditionCode, ...]
    acknowledged_at: str
    #: Free-text description of the client that captured it, for the audit trail. Never load-
    #: bearing.
    user_agent: str | None = None

    @property
    def acknowledged(self) -> bool:
        """The derived ``conditions_acknowledged == true`` (§1).

        Derived from the stored codes rather than stored as a boolean, so the flag cannot be true
        while the set is incomplete. Completeness itself is checked at acknowledgement time by
        ``domain.conditions.is_acknowledgement_complete``; this property is the record's answer.
        """
        return bool(self.acknowledged_codes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "conditions_version": self.conditions_version,
            "acknowledged_codes": [code.value for code in self.acknowledged_codes],
            "acknowledged_at": self.acknowledged_at,
            "acknowledged": self.acknowledged,
        }


@dataclass(frozen=True, slots=True)
class IdentityConfirmation:
    """That identity was confirmed, when, and how much friction it took.

    Deliberately holds **no** name and no email address. The profile is the system of record for
    both; copying them here would create a second copy of personal data that has to be kept in step
    with the first, inside a record an assessor reads.
    """

    confirmed_at: str
    #: The account-level flag as it stood at confirmation time.
    email_confirmed: bool
    #: Whether the learner also typed their email address, rather than relying on the account flag.
    email_supplied: bool = False
    #: How many confirmations were rejected before this one succeeded. Surfaces as an anomaly.
    rejected_attempts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "confirmed_at": self.confirmed_at,
            "email_confirmed": self.email_confirmed,
            "email_supplied": self.email_supplied,
            "rejected_attempts": self.rejected_attempts,
        }


@dataclass(frozen=True, slots=True)
class FormalResult:
    """The formal result, copied from UC-04's score and UC-05's decision.

    A *copy*, and read-only: UC-09 does not calculate a score, does not apply a pass mark and cannot
    change either. It records what the existing engines decided at the moment the formal attempt was
    resolved, because a review that happens three weeks later must show the assessor the result the
    learner actually got.

    ``passed`` is UC-05's decision. ``percentage`` and ``pass_mark`` are carried so an assessor can
    see the margin without a second round trip.
    """

    result_status: str
    passed: bool
    calculated_at: str
    percentage: float | None = None
    pass_mark: float | None = None
    total_marks: float | None = None
    maximum_marks: float | None = None
    score_status: str | None = None
    result_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_status": self.result_status,
            "passed": self.passed,
            "calculated_at": self.calculated_at,
            "percentage": self.percentage,
            "pass_mark": self.pass_mark,
            "total_marks": self.total_marks,
            "maximum_marks": self.maximum_marks,
            "score_status": self.score_status,
            "result_id": self.result_id,
        }


@dataclass(frozen=True, slots=True)
class DisconnectRecord:
    """How a formal attempt ended when nobody pressed submit (§5).

    Kept as its own structure because an assessor asks three separate questions about a disconnect:
    when the session was last seen, who reported it, and what the state that got submitted looked
    like.
    """

    detected_at: str
    #: Who reported it: the learner's own client, the platform session monitor, or an operator.
    reported_by: str
    #: The last instant the authoritative session was known to be alive, when known.
    last_seen_at: str | None = None
    #: The autosave instant of the state that was submitted.
    autosaved_at: str | None = None
    answered_questions: int | None = None
    total_questions: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "detected_at": self.detected_at,
            "reported_by": self.reported_by,
            "last_seen_at": self.last_seen_at,
            "autosaved_at": self.autosaved_at,
            "answered_questions": self.answered_questions,
            "total_questions": self.total_questions,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FormalAttempt:
    """A formal assessment sitting, from acknowledgement to certificate gate."""

    formal_attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    state: FormalAttemptState
    created_at: str
    updated_at: str
    #: Derived, never client-supplied: ``formal-attempt:<learner>:<quiz>:<created_at>``. Makes a
    #: replayed acknowledgement converge instead of creating a second record.
    idempotency_key: str = ""
    #: The UC-03 attempt. ``None`` until the attempt is started, unique across formal records
    #: afterwards.
    attempt_id: str | None = None
    attempt_number: int | None = None
    configuration_version_id: str | None = None
    #: The attempt this one retakes, when UC-08 asked for a formal retake. Recorded for lineage
    #: only.
    retake_of_attempt_id: str | None = None
    conditions: ConditionsAcknowledgement | None = None
    identity: IdentityConfirmation | None = None
    #: The authoritative device session. Sessions themselves live in their own repository; this is
    #: the
    #: pointer to the one that currently holds the lock.
    device_session_id: str | None = None
    started_at: str | None = None
    submitted_at: str | None = None
    submission_reason: FormalSubmissionReason | None = None
    disconnect: DisconnectRecord | None = None
    auto_submit_started_at: str | None = None
    result: FormalResult | None = None
    review_id: str | None = None
    certificate_workflow_triggered_at: str | None = None
    certificate_reference: str | None = None
    anomalies: tuple[FormalAnomaly, ...] = ()
    #: Rejected identity confirmations that happened before any successful one. Folded into
    #: ``IdentityConfirmation.rejected_attempts`` when confirmation finally succeeds, so a learner
    #: who mistyped their name three times before getting in leaves that visible to an assessor.
    pending_identity_rejections: int = 0
    #: Optimistic-concurrency token. Incremented by every transition; checked by every write.
    version: int = 1

    # ------------------------------------------------------------------
    # Derived facts
    # ------------------------------------------------------------------

    @property
    def open(self) -> bool:
        """The learner is inside the formal assessment workflow."""
        return self.state in OPEN_FORMAL_STATES

    @property
    def in_progress(self) -> bool:
        """The quiz is actually being sat. What the AI-coaching restriction keys on (§7)."""
        return self.state in IN_PROGRESS_FORMAL_STATES

    @property
    def submitted(self) -> bool:
        return self.state in SUBMITTED_FORMAL_STATES

    @property
    def conditions_acknowledged(self) -> bool:
        return self.conditions is not None and self.conditions.acknowledged

    @property
    def identity_confirmed(self) -> bool:
        return self.identity is not None

    @property
    def certificate_allowed(self) -> bool:
        """Whether a certificate may exist for this attempt (§11). Read-only, and the only source.
        """
        return self.state in CERTIFICATE_ALLOWED_STATES

    @property
    def auto_submitted(self) -> bool:
        return self.submission_reason is FormalSubmissionReason.DISCONNECT_AUTO_SUBMIT

    def acknowledged_version(self) -> str | None:
        return self.conditions.conditions_version if self.conditions else None

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def _moved(self, target: FormalAttemptState, *, now: str, **changes: Any) -> FormalAttempt:
        """Validate ``state -> target`` and return the new record.

        The single choke point. Every lifecycle method below goes through it, so there is no way to
        change ``state`` in this module without the transition table agreeing.
        """
        if not can_transition(self.state, target):
            raise InvalidStateTransitionError(
                formal_attempt_id=self.formal_attempt_id,
                current_state=self.state.value,
                target_state=target.value,
                allowed=tuple(
                    sorted(item.value for item in ALLOWED_TRANSITIONS.get(self.state, frozenset()))
                ),
            )
        return replace(
            self,
            state=target,
            updated_at=now,
            version=self.version + 1,
            **changes,
        )

    def acknowledge_conditions(
        self, acknowledgement: ConditionsAcknowledgement, *, now: str
    ) -> FormalAttempt:
        """Record the acknowledgement (§1).

        Legal from NOT_STARTED and from CONDITIONS_ACKNOWLEDGED — re-acknowledging a newer version
        of the conditions is a real thing that happens — and also from IDENTITY_CONFIRMED, where it
        *resets* the identity confirmation: if the conditions changed after identity was confirmed,
        the learner has to go through the whole gate again rather than starting on a stale
        confirmation.
        """
        resets_identity = self.state is FormalAttemptState.IDENTITY_CONFIRMED
        return self._moved(
            FormalAttemptState.CONDITIONS_ACKNOWLEDGED,
            now=now,
            conditions=acknowledgement,
            identity=None if resets_identity else self.identity,
        )

    def confirm_identity(self, confirmation: IdentityConfirmation, *, now: str) -> FormalAttempt:
        """Record a successful identity confirmation (§2)."""
        return self._moved(
            FormalAttemptState.IDENTITY_CONFIRMED,
            now=now,
            identity=confirmation,
        )

    def start(
        self,
        *,
        attempt_id: str,
        session_id: str,
        now: str,
        attempt_number: int | None = None,
        configuration_version_id: str | None = None,
        retake_of_attempt_id: str | None = None,
    ) -> FormalAttempt:
        """Go ACTIVE: UC-03 has delivered the attempt and one device holds the session (§3)."""
        return self._moved(
            FormalAttemptState.ACTIVE,
            now=now,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            configuration_version_id=configuration_version_id,
            retake_of_attempt_id=retake_of_attempt_id or self.retake_of_attempt_id,
            device_session_id=session_id,
            started_at=now,
        )

    def claim_auto_submit(self, disconnect: DisconnectRecord, *, now: str) -> FormalAttempt:
        """Claim the auto-submission for the first disconnect event (§5, §20).

        The durable claim that makes repeated disconnect events idempotent. Once this succeeds the
        attempt is AUTO_SUBMIT_IN_PROGRESS, which has exactly one way out — SUBMITTED — so there is
        no state in which a disconnected formal attempt can be resumed.
        """
        return self._moved(
            FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS,
            now=now,
            disconnect=disconnect,
            auto_submit_started_at=now,
        )

    def submit(
        self,
        *,
        reason: FormalSubmissionReason,
        now: str,
        submitted_at: str | None = None,
        disconnect: DisconnectRecord | None = None,
    ) -> FormalAttempt:
        """Record the submission. Reached from ACTIVE or from AUTO_SUBMIT_IN_PROGRESS (§5)."""
        return self._moved(
            FormalAttemptState.SUBMITTED,
            now=now,
            submitted_at=submitted_at or now,
            submission_reason=reason,
            disconnect=disconnect or self.disconnect,
        )

    def record_result(self, result: FormalResult, *, now: str) -> FormalAttempt:
        """Record the calculated result (§8). Pass/fail is decided in the next transition."""
        return self._moved(FormalAttemptState.RESULT_CALCULATED, now=now, result=result)

    def mark_passed(self, *, now: str) -> FormalAttempt:
        """A passing formal result — and nothing more. No certificate follows from here (§8)."""
        return self._moved(FormalAttemptState.PASSED, now=now)

    def mark_failed(self, *, now: str) -> FormalAttempt:
        return self._moved(FormalAttemptState.FAILED, now=now)

    def await_review(self, *, review_id: str, now: str) -> FormalAttempt:
        """PASS -> PENDING_REVIEW (§9). The state that must survive a queue outage."""
        return self._moved(FormalAttemptState.PENDING_REVIEW, now=now, review_id=review_id)

    def approve(self, *, now: str) -> FormalAttempt:
        """An authorised assessor approved the pass (§10)."""
        return self._moved(FormalAttemptState.APPROVED, now=now)

    def require_further_review(self, *, now: str) -> FormalAttempt:
        """The assessor escalated (§10). Terminal, and the certificate stays blocked."""
        return self._moved(FormalAttemptState.REQUIRES_FURTHER_REVIEW, now=now)

    def allow_certificate(
        self, *, now: str, certificate_reference: str | None = None
    ) -> FormalAttempt:
        """The certificate workflow has been triggered for an approved pass (§11, §12)."""
        return self._moved(
            FormalAttemptState.CERTIFICATE_ALLOWED,
            now=now,
            certificate_workflow_triggered_at=now,
            certificate_reference=certificate_reference,
        )

    # ------------------------------------------------------------------
    # Non-lifecycle changes
    # ------------------------------------------------------------------

    def with_anomaly(self, item: FormalAnomaly, *, now: str) -> FormalAttempt:
        """Record an observation without changing the state.

        Separate from the transitions on purpose: recording that a second device was turned away
        must never be able to move the assessment anywhere.
        """
        return replace(
            self,
            anomalies=record_anomaly(self.anomalies, item),
            updated_at=now,
            version=self.version + 1,
        )

    def with_anomalies(
        self, items: Iterable[FormalAnomaly], *, now: str
    ) -> FormalAttempt:
        """Record several observations as **one** change.

        Not a convenience: every mutation increments ``version``, and the repository's compare-and-
        set expects the stored record to be exactly one version behind the one being written. Two
        chained ``with_anomaly`` calls would produce a record two versions ahead and a write that
        looks like a lost update. So a caller with several observations records them together.
        """
        anomalies = self.anomalies
        for item in items:
            anomalies = record_anomaly(anomalies, item)
        if anomalies == self.anomalies:
            return self
        return replace(self, anomalies=anomalies, updated_at=now, version=self.version + 1)

    def with_session(self, session_id: str, *, now: str) -> FormalAttempt:
        """Point at a different authoritative session, without changing the state."""
        return replace(
            self, device_session_id=session_id, updated_at=now, version=self.version + 1
        )

    def with_identity_rejection(self, *, now: str) -> FormalAttempt:
        """Count a rejected identity confirmation.

        Kept on the record rather than only in the audit trail because the count is part of what an
        assessor sees, and because a rejection must not move the state: a learner who mistypes their
        name is still exactly where they were.
        """
        current = self.identity
        rejected = (current.rejected_attempts if current else 0) + 1
        identity = (
            replace(current, rejected_attempts=rejected)
            if current is not None
            else None
        )
        return replace(
            self,
            identity=identity,
            #: When identity has not yet been confirmed there is nowhere on the record to hold the
            #: count, so it is carried in the pending counter instead.
            pending_identity_rejections=(self.pending_identity_rejections + 1),
            updated_at=now,
            version=self.version + 1,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "formal_attempt_id": self.formal_attempt_id,
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "state": self.state.value,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "configuration_version_id": self.configuration_version_id,
            "retake_of_attempt_id": self.retake_of_attempt_id,
            "conditions_acknowledged": self.conditions_acknowledged,
            "conditions": self.conditions.as_dict() if self.conditions else None,
            "identity_confirmed": self.identity_confirmed,
            "identity": self.identity.as_dict() if self.identity else None,
            "device_session_id": self.device_session_id,
            "started_at": self.started_at,
            "submitted_at": self.submitted_at,
            "submission_reason": (
                self.submission_reason.value if self.submission_reason else None
            ),
            "auto_submitted": self.auto_submitted,
            "disconnect": self.disconnect.as_dict() if self.disconnect else None,
            "result": self.result.as_dict() if self.result else None,
            "review_id": self.review_id,
            "certificate_allowed": self.certificate_allowed,
            "certificate_workflow_triggered_at": self.certificate_workflow_triggered_at,
            "certificate_reference": self.certificate_reference,
            "anomalies": [item.as_dict() for item in self.anomalies],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }


def new_formal_attempt(
    *,
    formal_attempt_id: str,
    learner_id: str,
    course_id: str,
    quiz_id: str,
    idempotency_key: str,
    now: str,
) -> FormalAttempt:
    """A formal attempt in NOT_STARTED, ready to accept the conditions acknowledgement."""
    return FormalAttempt(
        formal_attempt_id=formal_attempt_id,
        learner_id=learner_id,
        course_id=course_id,
        quiz_id=quiz_id,
        state=FormalAttemptState.NOT_STARTED,
        created_at=now,
        updated_at=now,
        idempotency_key=idempotency_key,
    )
