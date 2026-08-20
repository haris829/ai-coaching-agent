"""Confirming identity before a formal assessment (§2).

    conditions acknowledged  ->  name matches the profile exactly  ->  account email confirmed
                                              ->  IDENTITY_CONFIRMED

The comparison itself is a pure function in ``domain.identity``; this service fetches the profile,
applies it, and turns a refusal into the right error and the right records.

THREE THINGS IT DOES BESIDES SAYING YES OR NO
---------------------------------------------
**It refuses in a fixed order.** Conditions first, then the name, then the email confirmation. A
learner who has not acknowledged the conditions is told that rather than being told their name is
wrong, and the order does not vary with the data — so the endpoint cannot be used to probe anything.

**It counts rejections.** A rejected confirmation leaves the state untouched (a mistyped name is not
a state change) but increments a counter on the record, which becomes an anomaly flag when
confirmation eventually succeeds. An assessor reviewing the pass can then see that identity took
three goes, which is information they may want and which no audit query should be needed to find.

**It records nothing personal.** No name, no email address — not on the record, not in an audit
line, not in an error. The profile remains the single place both live. What is stored is that a
comparison happened and what it concluded.

FAILURE DOES NOT DEGRADE
------------------------
If the profile source cannot be reached, ``LearnerProfileUnavailableError`` propagates as a
retryable 503. "We could not check the learner's name" never becomes "the name matched".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.formal_assessment.domain.anomalies import anomaly
from app.modules.formal_assessment.domain.attempt import (
    FormalAttempt,
    IdentityConfirmation,
)
from app.modules.formal_assessment.domain.enums import (
    FormalAnomalyCode,
    FormalAttemptState,
    FormalAuditEvent,
)
from app.modules.formal_assessment.domain.errors import (
    ConcurrentModificationError,
    EmailNotConfirmedError,
    FormalAttemptAlreadyStartedError,
    IdentityMismatchError,
    IdentityNotConfirmedError,
    LearnerProfileNotFoundError,
)
from app.modules.formal_assessment.domain.identity import (
    IdentityCheck,
    IdentitySubmission,
    check_identity,
)
from app.modules.formal_assessment.integration.audit import FormalAuditLog, safe_record
from app.modules.formal_assessment.integration.profiles import LearnerProfileProvider
from app.modules.formal_assessment.repositories.protocols import FormalAttemptRepository
from app.modules.formal_assessment.services.conditions_service import FormalConditionsService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IdentityOutcome:
    """A successful confirmation and the record it produced."""

    formal_attempt: FormalAttempt
    check: IdentityCheck

    def as_dict(self) -> dict[str, Any]:
        return {**self.formal_attempt.as_dict(), "identity_check": self.check.as_dict()}


class FormalIdentityService:
    def __init__(
        self,
        *,
        attempts: FormalAttemptRepository,
        profiles: LearnerProfileProvider,
        conditions: FormalConditionsService,
        audit: FormalAuditLog,
        clock: Clock,
    ) -> None:
        self._attempts = attempts
        self._profiles = profiles
        self._conditions = conditions
        self._audit = audit
        self._clock = clock

    async def confirm(
        self,
        *,
        learner_id: str,
        quiz_id: str,
        submission: IdentitySubmission,
    ) -> IdentityOutcome:
        """Confirm the learner's identity for their open formal attempt (§2)."""
        record = await self._require_open_record(learner_id, quiz_id)
        # Re-checked here rather than trusted from the acknowledgement step: the conditions may have
        # been
        # re-versioned in between, and an identity confirmed against superseded conditions must not
        # be
        # able to start an assessment.
        self._conditions.require_acknowledged(record)

        profile = await self._profiles.get_profile(learner_id)
        if profile is None:
            raise LearnerProfileNotFoundError(learner_id)

        check = check_identity(submission=submission, profile=profile)
        now = to_iso(self._clock.now())

        if check.mismatched_fields:
            await self._record_rejection(record, now=now, reason="IDENTITY_MISMATCH", check=check)
            raise IdentityMismatchError(check.mismatch_codes)

        if not check.email_confirmed:
            # The name matched; the account is simply not confirmed. Recorded as a rejection too —
            # it is
            # a failed attempt to pass the gate, and an assessor should see that it happened.
            await self._record_rejection(record, now=now, reason="EMAIL_NOT_CONFIRMED", check=check)
            raise EmailNotConfirmedError(learner_id)

        confirmation = IdentityConfirmation(
            confirmed_at=now,
            email_confirmed=check.email_confirmed,
            email_supplied=submission.email is not None,
            rejected_attempts=record.pending_identity_rejections,
        )
        stored = await self._save(record.confirm_identity(confirmation, now=now))
        if record.pending_identity_rejections:
            # A second write rather than a second mutation of the same record: each save is a
            # compare-and-set
            # against exactly one prior version, so an anomaly cannot be mistaken for a lost update.
            stored = await self._save(
                stored.with_anomaly(
                    anomaly(
                        FormalAnomalyCode.IDENTITY_CONFIRMATION_RETRIED,
                        observed_at=now,
                        rejected_attempts=record.pending_identity_rejections,
                    ),
                    now=now,
                )
            )

        await safe_record(
            self._audit,
            FormalAuditEvent.IDENTITY_CONFIRMED,
            formal_attempt_id=stored.formal_attempt_id,
            learner_id=learner_id,
            quiz_id=quiz_id,
            course_id=stored.course_id,
            email_confirmed=True,
            email_supplied=submission.email is not None,
            rejected_attempts=record.pending_identity_rejections,
            state=stored.state.value,
        )
        return IdentityOutcome(formal_attempt=stored, check=check)

    def require_confirmed(self, formal_attempt: FormalAttempt) -> None:
        """The gate the start path calls (§2).

        Deliberately checks the *record* rather than re-running the comparison: re-comparing at
        start time would need the learner to type their name again, and the record is what the audit
        trail already attests to.
        """
        if not formal_attempt.identity_confirmed:
            raise IdentityNotConfirmedError(
                learner_id=formal_attempt.learner_id, quiz_id=formal_attempt.quiz_id
            )

    async def _require_open_record(self, learner_id: str, quiz_id: str) -> FormalAttempt:
        record = await self._attempts.find_open_for_quiz(learner_id, quiz_id)
        if record is None:
            # No open record means the conditions were never acknowledged for this sitting: that is
            # the
            # first gate, and it is the one to report.
            raise IdentityNotConfirmedError(learner_id=learner_id, quiz_id=quiz_id)
        if record.state in (
            FormalAttemptState.ACTIVE,
            FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS,
        ):
            raise FormalAttemptAlreadyStartedError(
                formal_attempt_id=record.formal_attempt_id, state=record.state.value
            )
        return record

    async def _record_rejection(
        self, record: FormalAttempt, *, now: str, reason: str, check: IdentityCheck
    ) -> None:
        """Count a failed confirmation without moving the state.

        Best effort: if the counter cannot be written the refusal still stands. Failing the
        learner's confirmation because a counter could not be incremented would be the wrong trade,
        and the audit line below is the durable record either way.
        """
        try:
            await self._save(record.with_identity_rejection(now=now))
        except (ConcurrentModificationError, Exception):  # noqa: B014 - see the docstring
            logger.warning(
                "formal.identity.rejection_not_recorded",
                extra={"formal_attempt_id": record.formal_attempt_id, "reason": reason},
            )

        await safe_record(
            self._audit,
            FormalAuditEvent.IDENTITY_REJECTED,
            formal_attempt_id=record.formal_attempt_id,
            learner_id=record.learner_id,
            quiz_id=record.quiz_id,
            course_id=record.course_id,
            reason=reason,
            mismatched_fields=list(check.mismatch_codes),
            email_confirmed=check.email_confirmed,
            state=record.state.value,
        )

    async def _save(self, record: FormalAttempt) -> FormalAttempt:
        try:
            return await self._attempts.save(record)
        except ConcurrentModificationError:
            fresh = await self._attempts.get(record.formal_attempt_id)
            if fresh is None:  # pragma: no cover - there is no delete
                raise
            # Re-apply onto the winner rather than overwriting it: the only fields this service
            # changes
            # are the identity ones, so replaying them on the fresh record cannot lose someone
            # else's
            # write.
            return await self._attempts.save(
                replace(
                    fresh,
                    state=record.state,
                    identity=record.identity,
                    pending_identity_rejections=record.pending_identity_rejections,
                    anomalies=record.anomalies,
                    updated_at=record.updated_at,
                    version=fresh.version + 1,
                )
            )
