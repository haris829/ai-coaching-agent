"""Administrator additional-attempt grants (§11, §12, §14).

A grant gives **one learner** extra attempts at **one quiz on one course**. It is a record in
UC-08's own store and it does not, and cannot, change the quiz configuration: the UC-01 port this
module holds is read-only, so there is no code path anywhere in UC-08 that could write a
``maximum_attempts``. That is what makes §11's requirement structural rather than a convention:

::

    before   global maximum 2   ·   learner A entitlement 2   ·   learner B entitlement 2
    grant    +1 to learner A on quiz Q
    after    global maximum 2   ·   learner A entitlement 3   ·   learner B entitlement 2
             ▲ unchanged                                        ▲ unaffected

SAFETY (§12)
------------
* **Scope** — every grant carries learner + course + quiz, and the allowance only ever reads
  grants for all three. The course and quiz are validated against UC-01 before the grant is
  written, so a grant cannot be issued for a course the quiz does not belong to.
* **Duplication** — an idempotency key is *required*. Grants are the one operation where a derived
  key would be wrong: two identical grants a week apart can both be legitimate, and nothing in the
  domain distinguishes that from a double-submitted form. A replayed key returns the stored grant;
  the same key with different content is refused rather than silently ignored.
* **Traceability** — ``granted_by`` comes from the authorisation seam, never from the request body,
  and every grant and revocation is written to the audit port as well as to the record.
* **Negative and zero values** — rejected at the schema and again here, so a grant can never
  reduce an allowance. Revocation is the only way to take one back, and it is a status transition
  that keeps the record.
* **Partial application** — the repository contract requires the insert to be atomic. A failed
  insert leaves nothing, so a retry with the same key creates exactly one grant (§14).

REVOKING A SPENT GRANT
----------------------
Refused. If the learner has already used the attempt the grant conferred, withdrawing it would
push their used count above their entitlement — a state no other rule in this system can produce
and that every consumer of the allowance would have to be taught to handle. The grant stays, and
the audit trail records who issued it and why.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.retakes.domain.errors import (
    CourseNotFoundError,
    DuplicateGrantError,
    GrantAlreadyRevokedError,
    GrantConsumedError,
    GrantIdempotencyKeyReusedError,
    GrantNotFoundError,
    InvalidGrantSizeError,
    QuizNotFoundError,
)
from app.modules.retakes.domain.grants import AdditionalAttemptGrant, total_granted_attempts
from app.modules.retakes.domain.idempotency import grant_key
from app.modules.retakes.ids import IdGenerator
from app.modules.retakes.integration.audit import RetakeAuditLog
from app.modules.retakes.integration.uc01 import ConfigurationProvider
from app.modules.retakes.integration.uc03 import AttemptProvider
from app.modules.retakes.repositories.protocols import GrantRepository, RetakeRequestRepository
from app.modules.retakes.services.allowance_service import AttemptAllowanceService

logger = get_logger(__name__)


class GrantService:
    def __init__(
        self,
        *,
        grants: GrantRepository,
        configurations: ConfigurationProvider,
        attempts: AttemptProvider,
        retakes: RetakeRequestRepository,
        allowances: AttemptAllowanceService,
        audit: RetakeAuditLog,
        clock: Clock,
        new_id: IdGenerator,
        max_additional_attempts: int,
    ) -> None:
        self._grants = grants
        self._configurations = configurations
        self._attempts = attempts
        self._retakes = retakes
        self._allowances = allowances
        self._audit = audit
        self._clock = clock
        self._new_id = new_id
        self._max_additional_attempts = max_additional_attempts

    # ------------------------------------------------------------- reading

    async def get(self, grant_id: str) -> AdditionalAttemptGrant:
        stored = await self._grants.get(grant_id)
        if stored is None:
            raise GrantNotFoundError(grant_id)
        return stored

    async def list_for_learner_quiz(
        self, learner_id: str, quiz_id: str
    ) -> tuple[str, tuple[AdditionalAttemptGrant, ...]]:
        """Every grant for a learner on a quiz, with the course it resolved to.

        The course is resolved from UC-01 rather than accepted from the caller, so a read can never
        be scoped to a course the quiz does not belong to.
        """
        course_id = await self._resolve_course(quiz_id)
        return course_id, await self._grants.list_for_learner_quiz(learner_id, course_id, quiz_id)

    # ------------------------------------------------------------ granting

    async def grant(
        self,
        *,
        learner_id: str,
        quiz_id: str,
        additional_attempts: int,
        granted_by: str,
        idempotency_key: str,
        course_id: str | None = None,
        reason: str | None = None,
    ) -> tuple[AdditionalAttemptGrant, bool]:
        """Grant additional attempts. Returns the grant and whether it was a replay."""
        if (
            not isinstance(additional_attempts, int)
            or isinstance(additional_attempts, bool)
            or additional_attempts < 1
            or additional_attempts > self._max_additional_attempts
        ):
            # Also enforced by the schema; repeated here because the service is callable directly
            # by a host application that has not been through the HTTP layer.
            raise InvalidGrantSizeError(additional_attempts, self._max_additional_attempts)

        resolved_course = await self._resolve_course(quiz_id)
        if course_id and course_id != resolved_course:
            raise CourseNotFoundError(course_id)

        key = grant_key(learner_id, quiz_id, idempotency_key)
        existing = await self._grants.get_by_idempotency_key(key)
        if existing is not None:
            return self._verify_replay(existing, resolved_course, additional_attempts, key), True

        now = to_iso(self._clock.now())
        grant = AdditionalAttemptGrant(
            grant_id=self._new_id(),
            learner_id=learner_id,
            course_id=resolved_course,
            quiz_id=quiz_id,
            additional_attempts=additional_attempts,
            granted_by=granted_by,
            idempotency_key=key,
            granted_at=now,
            reason=reason,
        )

        try:
            stored = await self._grants.insert(grant)
        except DuplicateGrantError:
            # Lost a race with an identical concurrent grant. Read the winner rather than
            # granting twice.
            winner = await self._grants.get_by_idempotency_key(key)
            if winner is None:  # pragma: no cover - the constraint says it exists
                raise
            return self._verify_replay(winner, resolved_course, additional_attempts, key), True

        await self._audit.record(
            "additional_attempt_granted",
            grant_id=stored.grant_id,
            learner_id=stored.learner_id,
            course_id=stored.course_id,
            quiz_id=stored.quiz_id,
            additional_attempts=stored.additional_attempts,
            granted_by=stored.granted_by,
            reason=stored.reason,
        )
        return stored, False

    async def revoke(
        self, *, grant_id: str, revoked_by: str, reason: str | None = None
    ) -> AdditionalAttemptGrant:
        """Withdraw a grant whose attempts have not been used. See the module docstring."""
        grant = await self.get(grant_id)
        if not grant.active:
            raise GrantAlreadyRevokedError(grant_id)

        used = await self._allowances.attempts_used(
            learner_id=grant.learner_id,
            course_id=grant.course_id,
            quiz_id=grant.quiz_id,
        )
        configuration_maximum = await self._maximum_attempts(grant.learner_id, grant.quiz_id)
        remaining_grants = total_granted_attempts(
            tuple(
                other
                for other in await self._grants.list_for_learner_quiz(
                    grant.learner_id, grant.course_id, grant.quiz_id
                )
                if other.grant_id != grant.grant_id
            )
        )
        if configuration_maximum is not None:
            entitlement_without = configuration_maximum + remaining_grants
            if used > entitlement_without:
                raise GrantConsumedError(grant_id, used, entitlement_without)

        revoked = await self._grants.save(
            grant.revoked(at=to_iso(self._clock.now()), by=revoked_by)
        )
        await self._audit.record(
            "additional_attempt_revoked",
            grant_id=revoked.grant_id,
            learner_id=revoked.learner_id,
            course_id=revoked.course_id,
            quiz_id=revoked.quiz_id,
            additional_attempts=revoked.additional_attempts,
            revoked_by=revoked_by,
            reason=reason,
        )
        return revoked

    # ----------------------------------------------------------- internals

    def _verify_replay(
        self,
        existing: AdditionalAttemptGrant,
        course_id: str,
        additional_attempts: int,
        key: str,
    ) -> AdditionalAttemptGrant:
        """A replay must be the *same* grant, or it is a key collision, not a retry."""
        if (
            existing.course_id != course_id
            or existing.additional_attempts != additional_attempts
        ):
            raise GrantIdempotencyKeyReusedError(key)
        return existing

    async def _resolve_course(self, quiz_id: str) -> str:
        availability = await self._configurations.get_quiz_availability(quiz_id)
        if availability is None:
            raise QuizNotFoundError(quiz_id)
        return availability.course_id

    async def _maximum_attempts(self, learner_id: str, quiz_id: str) -> int | None:
        """The maximum the learner's allowance is actually computed from, read only.

        Deliberately the version locked to their most recent attempt, not today's active version:
        the revocation guard has to reason about the same entitlement the allowance reports, and
        those two numbers differ whenever a new configuration version has been published since the
        learner last attempted. Reading the active version here instead would let a grant be
        revoked out from under an attempt the learner had already used.
        """
        attempts = await self._attempts.list_attempts(learner_id, quiz_id)
        if attempts:
            latest = max(attempts, key=lambda attempt: attempt.attempt_number)
            locked = await self._configurations.get_locked_configuration(
                latest.configuration_version_id
            )
            if locked is not None:
                return locked.maximum_attempts
        active = await self._configurations.get_active_configuration(quiz_id)
        return active.maximum_attempts if active else None
