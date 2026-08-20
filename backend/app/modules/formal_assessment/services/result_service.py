"""Resolving the formal result — and stopping short of a certificate (§8, §9).

    UC-04 confirmed score  +  UC-05 pass/fail  ->  RESULT_CALCULATED  ->  PASSED  ->  PENDING_REVIEW
                                                                       \\-> FAILED  (terminal)

**No scoring happens here.** No marks are added, no pass mark is compared, no percentage is
recalculated. The service reads what UC-04 and UC-05 already decided, copies it onto the formal
attempt, and then does the single thing that is UC-09's own: it moves a *pass* to PENDING_REVIEW
instead of letting anything follow from it.

That last step is the requirement in one line of code — ``mark_passed`` then ``await_review`` — and
everything around it exists to make sure the line cannot be skipped. There is no transition from
PASSED
to anything except PENDING_REVIEW, no transition from PENDING_REVIEW except an assessor's decision,
and the certificate gate reads the state rather than the result.

WHY RESOLUTION IS LAZY AND REPEATABLE
-------------------------------------
Scoring is asynchronous. UC-04 confirms a score when it confirms it, and UC-05 determines a result
after that. So resolution is *attempted* after submission, again whenever anyone reads the attempt's
status, and again by the platform when it notifies UC-09 that scoring finished. Every attempt is
idempotent: an attempt that is not SUBMITTED is left alone, a score that is not confirmed defers,
and an already- resolved attempt returns unchanged.

``try_resolve`` swallows upstream failures and returns the record untouched, because an unreachable
scoring module must not turn a successful submission into an error the learner sees. ``resolve`` —
used by the paths that asked specifically for a resolution — lets the failure surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.formal_assessment.domain.attempt import FormalAttempt, FormalResult
from app.modules.formal_assessment.domain.enums import FormalAttemptState, FormalAuditEvent
from app.modules.formal_assessment.domain.errors import ConcurrentModificationError
from app.modules.formal_assessment.integration.audit import FormalAuditLog, safe_record
from app.modules.formal_assessment.integration.results import (
    PassFailResultProvider,
    ScoringResultProvider,
)
from app.modules.formal_assessment.repositories.protocols import FormalAttemptRepository
from app.modules.formal_assessment.services.review_service import FormalReviewService

logger = get_logger(__name__)


class ResolutionOutcome(StrEnum):
    """What one resolution attempt actually did."""

    #: The result was recorded and the pass/fail branch taken.
    RESOLVED = "RESOLVED"
    #: Scoring is not confirmed, or no pass/fail decision exists yet. Nothing was written.
    DEFERRED = "DEFERRED"
    #: The attempt was already resolved. Nothing was written.
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    #: The attempt is not in a state where a result can be recorded.
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class Resolution:
    """The record after a resolution attempt, and what the attempt achieved."""

    formal_attempt: FormalAttempt
    outcome: ResolutionOutcome
    #: Why a resolution deferred, for an operator watching a stuck attempt.
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.outcome is ResolutionOutcome.RESOLVED

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.formal_attempt.as_dict(),
            "resolution_outcome": self.outcome.value,
            "resolution_reason": self.reason,
        }


class FormalResultService:
    def __init__(
        self,
        *,
        attempts: FormalAttemptRepository,
        scores: ScoringResultProvider,
        results: PassFailResultProvider,
        reviews: FormalReviewService,
        audit: FormalAuditLog,
        clock: Clock,
    ) -> None:
        self._attempts = attempts
        self._scores = scores
        self._results = results
        self._reviews = reviews
        self._audit = audit
        self._clock = clock

    async def try_resolve(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        """Attempt a resolution and never fail because of it.

        Used on the submission path: the learner's submission has already succeeded, and an
        unreachable UC-04 must not turn that into an error. The record comes back untouched and the
        next status read tries again.
        """
        try:
            return (await self.resolve(formal_attempt)).formal_attempt
        except Exception as error:  # noqa: BLE001 - see the docstring
            logger.warning(
                "formal.result.resolution_deferred",
                extra={
                    "formal_attempt_id": formal_attempt.formal_attempt_id,
                    "error": type(error).__name__,
                },
            )
            return formal_attempt

    async def resolve(self, formal_attempt: FormalAttempt) -> Resolution:
        """Record the formal result and take the pass/fail branch (§8, §9)."""
        record = formal_attempt

        if record.state in (
            FormalAttemptState.PASSED,
            FormalAttemptState.FAILED,
            FormalAttemptState.PENDING_REVIEW,
            FormalAttemptState.APPROVED,
            FormalAttemptState.REQUIRES_FURTHER_REVIEW,
            FormalAttemptState.CERTIFICATE_ALLOWED,
        ):
            return Resolution(formal_attempt=record, outcome=ResolutionOutcome.ALREADY_RESOLVED)

        if record.state is FormalAttemptState.RESULT_CALCULATED:
            # The result was recorded but the branch was not taken — a process died between two
            # writes.
            # Continue from where it stopped rather than starting again.
            return await self._branch(record)

        if record.state is not FormalAttemptState.SUBMITTED or record.attempt_id is None:
            return Resolution(
                formal_attempt=record,
                outcome=ResolutionOutcome.NOT_APPLICABLE,
                reason="ATTEMPT_NOT_SUBMITTED",
            )

        score = await self._scores.get_score(record.attempt_id)
        if score is None or not score.confirmed:
            return Resolution(
                formal_attempt=record,
                outcome=ResolutionOutcome.DEFERRED,
                reason="SCORE_NOT_CONFIRMED",
            )

        decision = await self._results.get_result(record.attempt_id)
        if decision is None or not decision.determined:
            # UC-05 uses PENDING for "no safe decision is possible yet". Recording that as a formal
            # result
            # would create an attempt with a result and no outcome, so it waits.
            return Resolution(
                formal_attempt=record,
                outcome=ResolutionOutcome.DEFERRED,
                reason="RESULT_NOT_DETERMINED",
            )

        now = to_iso(self._clock.now())
        result = FormalResult(
            result_status=decision.status,
            passed=decision.passed,
            calculated_at=decision.determined_at or now,
            percentage=decision.percentage if decision.percentage is not None else score.percentage,
            pass_mark=decision.pass_mark,
            total_marks=score.total_marks,
            maximum_marks=score.maximum_marks,
            score_status=score.status,
            result_id=decision.result_id,
        )

        try:
            record = await self._attempts.save(record.record_result(result, now=now))
        except ConcurrentModificationError:
            # Another resolution attempt is running. Read the winner and continue from whatever it
            # did:
            # two resolutions must converge on one result, and the result itself is immutable
            # upstream.
            fresh = await self._attempts.get(record.formal_attempt_id)
            if fresh is None:  # pragma: no cover - there is no delete
                raise
            if fresh.state is FormalAttemptState.SUBMITTED:
                return Resolution(
                    formal_attempt=fresh,
                    outcome=ResolutionOutcome.DEFERRED,
                    reason="CONCURRENT_RESOLUTION",
                )
            return await self.resolve(fresh)

        await safe_record(
            self._audit,
            FormalAuditEvent.RESULT_CALCULATED,
            formal_attempt_id=record.formal_attempt_id,
            learner_id=record.learner_id,
            quiz_id=record.quiz_id,
            attempt_id=record.attempt_id,
            result_status=result.result_status,
            passed=result.passed,
            percentage=result.percentage,
            pass_mark=result.pass_mark,
            score_status=result.score_status,
        )

        return await self._branch(record)

    async def resolve_by_id(self, formal_attempt_id: str) -> Resolution:
        """Resolve by identifier, for the platform notifying UC-09 that scoring has finished."""
        from app.modules.formal_assessment.domain.errors import FormalAttemptNotFoundError

        record = await self._attempts.get(formal_attempt_id)
        if record is None:
            raise FormalAttemptNotFoundError(formal_attempt_id)
        return await self.resolve(record)

    async def _branch(self, record: FormalAttempt) -> Resolution:
        """RESULT_CALCULATED -> PASSED -> PENDING_REVIEW, or -> FAILED (§8, §9).

        The pass branch is two transitions rather than one because they mean different things and
        are audited separately: the learner passed, *and* the pass is now waiting for a human.
        Collapsing them would lose the distinction an assessor's queue depends on.
        """
        result = record.result
        # Only reachable from RESULT_CALCULATED, which always has a result. Defensive.
        if result is None:  # pragma: no cover
            return Resolution(
                formal_attempt=record,
                outcome=ResolutionOutcome.DEFERRED,
                reason="RESULT_MISSING",
            )

        now = to_iso(self._clock.now())

        if not result.passed:
            stored = await self._attempts.save(record.mark_failed(now=now))
            return Resolution(formal_attempt=stored, outcome=ResolutionOutcome.RESOLVED)

        passed = await self._attempts.save(record.mark_passed(now=now))

        # The review is created and persisted *before* the attempt moves to PENDING_REVIEW, so a
        # failure
        # between the two leaves an attempt in PASSED that the next resolution picks up — rather
        # than an
        # attempt claiming to be pending review with no review to find.
        review = await self._reviews.create_pending(passed)

        pending = await self._attempts.save(
            passed.await_review(review_id=review.review_id, now=to_iso(self._clock.now()))
        )

        await safe_record(
            self._audit,
            FormalAuditEvent.PENDING_REVIEW_CREATED,
            formal_attempt_id=pending.formal_attempt_id,
            learner_id=pending.learner_id,
            course_id=pending.course_id,
            quiz_id=pending.quiz_id,
            attempt_id=pending.attempt_id,
            review_id=review.review_id,
            percentage=result.percentage,
            auto_submitted=pending.auto_submitted,
            anomaly_count=len(pending.anomalies),
        )

        # Publishing to the assessor queue is a notification and is allowed to fail: the review is
        # already
        # durable, listed and reviewable, and the certificate is blocked either way (§13).
        await self._reviews.publish(review)

        return Resolution(formal_attempt=pending, outcome=ResolutionOutcome.RESOLVED)
