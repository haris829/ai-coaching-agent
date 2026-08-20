"""The AI coaching restriction, server-side (§7, §19).

    is_ai_coaching_allowed(learner_id, attempt_id)
        -> reads the learner's in-progress formal attempts from the database
        -> allowed, or blocked with a reason, an audit event and an anomaly flag

This is the check UC-07 calls and the check the endpoint exposes. **It reads persisted state and
nothing else** — no request field, no header, no client claim participates in the decision, so a
learner calling the coaching API directly with a hand-written request gets the same answer as one
clicking a button.

WHY IT IS ASKED PER REQUEST AND NOT CACHED
------------------------------------------
UC-07's own gate already re-checks its conditions on every coaching operation rather than only at
session start, for the same reason: a session that was legitimately opened is not a licence. A
learner may open a coaching session at 10:00 and start a formal assessment at 10:05, and the message
they send at 10:06 must be refused. So this check belongs on every operation that reaches a model,
and ``require_allowed`` is the form that makes forgetting it impossible — it raises rather than
returning a value a caller can ignore.

WHAT GETS RECORDED
------------------
A blocked request emits ``AI_COACHING_BLOCKED`` and flags the formal attempt with
``AI_COACHING_ATTEMPTED``. Both matter: the audit event is the platform-wide trail, and the anomaly
is what an assessor sees when they review the pass. A learner who tried to open Larry mid-assessment
is exactly the kind of thing a human approving that assessment should know about.

Recording never fails the answer. If the anomaly cannot be written, the request is still refused.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.formal_assessment.domain.anomalies import anomaly
from app.modules.formal_assessment.domain.coaching import (
    CoachingPermission,
    evaluate_coaching_permission,
)
from app.modules.formal_assessment.domain.enums import FormalAnomalyCode, FormalAuditEvent
from app.modules.formal_assessment.domain.errors import AiCoachingForbiddenError
from app.modules.formal_assessment.integration.audit import FormalAuditLog, safe_record
from app.modules.formal_assessment.repositories.protocols import FormalAttemptRepository

logger = get_logger(__name__)


class AiCoachingPolicyService:
    """UC-09's answer to "may Larry run for this learner right now?" (§7)."""

    def __init__(
        self,
        *,
        attempts: FormalAttemptRepository,
        audit: FormalAuditLog,
        clock: Clock,
    ) -> None:
        self._attempts = attempts
        self._audit = audit
        self._clock = clock

    async def is_ai_coaching_allowed(
        self, *, learner_id: str, attempt_id: str | None = None
    ) -> CoachingPermission:
        """The check itself (§7).

        Learner-scoped: an in-progress formal assessment blocks coaching on *any* attempt, including
        the learner's older, submitted, fully scored ones. See ``domain.coaching`` for why an
        attempt- scoped check would miss the case that matters.
        """
        in_progress = await self._attempts.list_in_progress_for_learner(learner_id)
        permission = evaluate_coaching_permission(
            active_formal_attempts=in_progress, requested_attempt_id=attempt_id
        )

        if not permission.allowed:
            await self._record_block(
                learner_id=learner_id, attempt_id=attempt_id, permission=permission
            )
        return permission

    async def require_allowed(
        self, *, learner_id: str, attempt_id: str | None = None
    ) -> CoachingPermission:
        """The same check as an assertion, for a caller that is about to reach a model (§7, §19).

        UC-07 binds this into its authorisation gate. It raises 403 ``AI_COACHING_FORBIDDEN``, which
        is what a direct call to the coaching API receives.
        """
        permission = await self.is_ai_coaching_allowed(learner_id=learner_id, attempt_id=attempt_id)
        if not permission.allowed:
            raise AiCoachingForbiddenError(
                learner_id=learner_id,
                reason=permission.reason.value if permission.reason else "FORMAL_ASSESSMENT",
                formal_attempt_id=permission.formal_attempt_id,
            )
        return permission

    async def _record_block(
        self, *, learner_id: str, attempt_id: str | None, permission: CoachingPermission
    ) -> None:
        """Audit the refusal and flag the assessment. Never changes the refusal."""
        await safe_record(
            self._audit,
            FormalAuditEvent.AI_COACHING_BLOCKED,
            learner_id=learner_id,
            requested_attempt_id=attempt_id,
            formal_attempt_id=permission.formal_attempt_id,
            quiz_id=permission.quiz_id,
            reason=permission.reason.value if permission.reason else None,
        )

        if not permission.formal_attempt_id:  # pragma: no cover - a block always names an attempt
            return

        now = to_iso(self._clock.now())
        try:
            record = await self._attempts.get(permission.formal_attempt_id)
            if record is None:  # pragma: no cover - there is no delete
                return
            await self._attempts.save(
                record.with_anomaly(
                    anomaly(
                        FormalAnomalyCode.AI_COACHING_ATTEMPTED,
                        observed_at=now,
                        requested_attempt_id=attempt_id,
                    ),
                    now=now,
                )
            )
        except Exception:  # noqa: BLE001 - the refusal stands whether or not the flag was written
            logger.warning(
                "formal.anomaly_not_recorded",
                extra={
                    "formal_attempt_id": permission.formal_attempt_id,
                    "code": FormalAnomalyCode.AI_COACHING_ATTEMPTED.value,
                },
            )
