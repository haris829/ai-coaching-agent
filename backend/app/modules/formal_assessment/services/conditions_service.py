"""Acknowledging the formal assessment conditions (§1).

    GET  conditions      -> the seven conditions and the version to acknowledge
    POST acknowledgement -> validated, recorded, and the formal attempt record created

WHAT "VALIDATED" MEANS HERE
---------------------------
The specification asks for a business rule that checks ``conditions_acknowledged == true`` before a
formal attempt may start. Two things make that rule real rather than decorative:

1. **The backend derives the boolean.** A request lists the condition codes the learner ticked; the
   domain checks the set against the seven required ones. A client cannot send
   ``conditions_acknowledged: true`` because there is no such field to send.
2. **The acknowledgement is versioned.** It is recorded against the conditions version it was made
   against, and ``require_acknowledged`` — the gate the start path calls — refuses an
   acknowledgement of a superseded version. Re-versioning the conditions therefore invalidates every
   acknowledgement of the old wording, which is the only interpretation under which the record means
   anything.

THE RECORD IS CREATED HERE
--------------------------
Acknowledging the conditions is the first step of a formal attempt, so this is where the formal
attempt record comes into existence — in ``CONDITIONS_ACKNOWLEDGED``, with no UC-03 attempt behind
it yet. That ordering is what lets the identity step and the start step both find a record to attach
to, and it is why "one open formal attempt per learner and quiz" is the constraint that makes a
duplicated acknowledgement converge instead of forking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.formal_assessment.domain.attempt import (
    ConditionsAcknowledgement,
    FormalAttempt,
    new_formal_attempt,
)
from app.modules.formal_assessment.domain.conditions import (
    FormalConditionCode,
    formal_conditions,
    is_acknowledgement_complete,
    missing_conditions,
    normalise_condition_codes,
)
from app.modules.formal_assessment.domain.enums import FormalAttemptState, FormalAuditEvent
from app.modules.formal_assessment.domain.errors import (
    ConcurrentModificationError,
    ConditionsAcknowledgementIncompleteError,
    ConditionsNotAcknowledgedError,
    DuplicateFormalAttemptError,
    FormalAttemptAlreadyStartedError,
)
from app.modules.formal_assessment.domain.idempotency import formal_attempt_key
from app.modules.formal_assessment.ids import IdGenerator
from app.modules.formal_assessment.integration.audit import FormalAuditLog, safe_record
from app.modules.formal_assessment.repositories.protocols import FormalAttemptRepository
from app.modules.formal_assessment.services.policy_service import FormalPolicyService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AcknowledgementOutcome:
    """The record after acknowledgement, and whether this call created it."""

    formal_attempt: FormalAttempt
    created: bool
    conditions_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.formal_attempt.as_dict(),
            "created": self.created,
            "conditions_version": self.conditions_version,
        }


class FormalConditionsService:
    def __init__(
        self,
        *,
        attempts: FormalAttemptRepository,
        policies: FormalPolicyService,
        audit: FormalAuditLog,
        clock: Clock,
        new_id: IdGenerator,
        conditions_version: str,
    ) -> None:
        self._attempts = attempts
        self._policies = policies
        self._audit = audit
        self._clock = clock
        self._new_id = new_id
        self._conditions_version = conditions_version

    @property
    def conditions_version(self) -> str:
        return self._conditions_version

    async def describe(self, quiz_id: str) -> dict[str, Any]:
        """The conditions a learner must acknowledge for this quiz.

        Includes the quiz's policy so a client knows whether human review and assessor approval
        apply before it tells the learner what to expect. This is the read a conditions screen is
        built from; the screen itself is not UC-09's to build.
        """
        policy = await self._policies.get_policy(quiz_id)
        return {
            "quiz_id": quiz_id,
            "course_id": policy.course_id,
            "is_formal_assessment": policy.is_formal_assessment,
            "requires_human_review": policy.requires_human_review,
            "requires_assessor_approval": policy.requires_assessor_approval,
            **formal_conditions(self._conditions_version),
        }

    async def acknowledge(
        self,
        *,
        learner_id: str,
        quiz_id: str,
        acknowledged_codes: object,
        user_agent: str | None = None,
    ) -> AcknowledgementOutcome:
        """Record the learner's acknowledgement of the formal conditions (§1)."""
        policy = await self._policies.require_available(quiz_id)

        codes = normalise_condition_codes(acknowledged_codes)
        if not is_acknowledgement_complete(codes):
            # Refused before anything is written: an incomplete acknowledgement leaves no trace of a
            # half-agreed set of conditions.
            raise ConditionsAcknowledgementIncompleteError(missing_conditions(codes))

        now = to_iso(self._clock.now())
        acknowledgement = ConditionsAcknowledgement(
            conditions_version=self._conditions_version,
            acknowledged_codes=codes,
            acknowledged_at=now,
            user_agent=user_agent,
        )

        existing = await self._attempts.find_open_for_quiz(learner_id, quiz_id)
        if existing is not None:
            if existing.state in (
                FormalAttemptState.ACTIVE,
                FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS,
            ):
                # The assessment is under way. Re-acknowledging cannot change conditions that are
                # already in force, and must not reset a live attempt's gate.
                raise FormalAttemptAlreadyStartedError(
                    formal_attempt_id=existing.formal_attempt_id, state=existing.state.value
                )
            updated = await self._save_acknowledgement(existing, acknowledgement, now=now)
            await self._audit_acknowledged(updated, created=False)
            return AcknowledgementOutcome(
                formal_attempt=updated, created=False, conditions_version=self._conditions_version
            )

        record = new_formal_attempt(
            formal_attempt_id=self._new_id(),
            learner_id=learner_id,
            course_id=policy.course_id,
            quiz_id=quiz_id,
            idempotency_key=formal_attempt_key(learner_id, quiz_id),
            now=now,
        ).acknowledge_conditions(acknowledgement, now=now)

        try:
            stored = await self._attempts.insert(record)
        except DuplicateFormalAttemptError:
            # Two acknowledgements raced. The constraint picked a winner; read it and acknowledge
            # onto
            # that record rather than reporting a conflict the learner did not cause.
            winner = await self._attempts.find_open_for_quiz(learner_id, quiz_id)
            if winner is None:  # pragma: no cover - the constraint fired, so a record exists
                raise
            stored = await self._save_acknowledgement(winner, acknowledgement, now=now)
            await self._audit_acknowledged(stored, created=False)
            return AcknowledgementOutcome(
                formal_attempt=stored, created=False, conditions_version=self._conditions_version
            )

        await self._audit_acknowledged(stored, created=True)
        return AcknowledgementOutcome(
            formal_attempt=stored, created=True, conditions_version=self._conditions_version
        )

    def raise_not_acknowledged(self, *, learner_id: str, quiz_id: str) -> None:
        """Raise the conditions refusal for a learner with no acknowledgement at all (§1).

        Used by the start path, which can reach that situation without a record to inspect. It lives
        here so the wording, the code and the context of "you have not acknowledged the conditions"
        exist in one place rather than in every caller that can hit it.
        """
        raise ConditionsNotAcknowledgedError(
            learner_id=learner_id,
            quiz_id=quiz_id,
            required_version=self._conditions_version,
        )

    def require_acknowledged(self, formal_attempt: FormalAttempt) -> None:
        """The gate every later step calls (§1).

        Refuses when nothing was acknowledged, and when what was acknowledged is a superseded
        version of the conditions. Both are the same refusal to a client — go and acknowledge the
        conditions — and the context says which it was.
        """
        if not formal_attempt.conditions_acknowledged:
            raise ConditionsNotAcknowledgedError(
                learner_id=formal_attempt.learner_id,
                quiz_id=formal_attempt.quiz_id,
                required_version=self._conditions_version,
            )
        acknowledged_version = formal_attempt.acknowledged_version()
        if acknowledged_version != self._conditions_version:
            raise ConditionsNotAcknowledgedError(
                learner_id=formal_attempt.learner_id,
                quiz_id=formal_attempt.quiz_id,
                required_version=self._conditions_version,
                acknowledged_version=acknowledged_version,
            )

    async def _save_acknowledgement(
        self, record: FormalAttempt, acknowledgement: ConditionsAcknowledgement, *, now: str
    ) -> FormalAttempt:
        updated = record.acknowledge_conditions(acknowledgement, now=now)
        try:
            return await self._attempts.save(updated)
        except ConcurrentModificationError:
            # Somebody wrote to the record between the read and the save. Re-read and apply once
            # more;
            # acknowledging is idempotent in effect, so a single retry converges rather than
            # looping.
            fresh = await self._attempts.get(record.formal_attempt_id)
            if fresh is None:  # pragma: no cover - the record cannot vanish; there is no delete
                raise
            return await self._attempts.save(
                fresh.acknowledge_conditions(acknowledgement, now=now)
            )

    async def _audit_acknowledged(self, record: FormalAttempt, *, created: bool) -> None:
        await safe_record(
            self._audit,
            FormalAuditEvent.FORMAL_CONDITIONS_ACKNOWLEDGED,
            formal_attempt_id=record.formal_attempt_id,
            learner_id=record.learner_id,
            course_id=record.course_id,
            quiz_id=record.quiz_id,
            conditions_version=self._conditions_version,
            acknowledged_codes=[
                code.value
                for code in (record.conditions.acknowledged_codes if record.conditions else ())
            ],
            record_created=created,
            state=record.state.value,
        )


def condition_codes(*codes: str) -> tuple[FormalConditionCode, ...]:
    """Parse condition codes for a caller that already has strings. Used by tests and by fixtures.
    """
    return normalise_condition_codes(codes)
