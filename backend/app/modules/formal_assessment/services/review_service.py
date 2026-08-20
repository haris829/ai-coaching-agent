"""Human review of a passing formal assessment (§9, §10, §13, §19, §20).

    create_pending  ->  publish (best effort)  ->  list_pending  ->  get_detail  ->  start  ->
    decide
                                                                                                 |
                                                            APPROVED --------------------------- +
                                                                |                                |
                                                    certificate workflow REQUIRES_FURTHER_REVIEW
                                                                                  (certificate stays
                                                                                  blocked)

FOUR PROPERTIES WORTH READING THE CODE FOR
------------------------------------------
**A pending review cannot be lost (§13).** ``create_pending`` persists before ``publish`` is ever
called, and ``publish`` cannot raise into the caller. A queue outage leaves a review that
``list_pending`` returns and ``list_unpublished`` flags for retry.

**One review per pass, one queue entry per review (§20).** ``formal_attempt_id`` is unique in the
repository, so a concurrent double resolution produces one review; the queue entry key is derived
from the review id, so a concurrent double publish produces one entry.

**Authorisation is checked twice, on every call (§10, §19).** Authentication (which assessor is
this?) happens in the HTTP dependency; authorisation (may they review *this course*?) happens here,
against the assessor directory, on every single operation — listing, reading, starting and deciding.
A valid token is never sufficient, and an assessor authorised at the start of a session does not
stay authorised for a course they were removed from.

**A decision is final (§20).** ``decide`` refuses a review that already has one, and the compare-
and-set on the review's version means two simultaneous decisions resolve to the first, with the
second told what the first was. There is no path that overwrites a decision, and no path from
REQUIRES_FURTHER_REVIEW to APPROVED.

WHAT THE REVIEW PAYLOAD CONTAINS, AND WHY IT CONTAINS THE LEARNER'S NAME
-----------------------------------------------------------------------
Everything §10 asks for: learner, assessment, score, responses, attempt, anomaly flags, submission
and disconnect information, and the supervision trail. It includes the learner's name and email
address — which no other part of UC-09 stores or logs — because an assessor confirming that the
right person sat the assessment cannot do that from an opaque identifier. It is read live from the
profile source for the authorised assessor who asked, and it is not copied into any UC-09 record.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from app.core.coercion import parse_enum
from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.formal_assessment.domain.attempt import FormalAttempt
from app.modules.formal_assessment.domain.enums import (
    AssessorDecision,
    FormalAttemptState,
    FormalAuditEvent,
)
from app.modules.formal_assessment.domain.errors import (
    AssessorNotAuthorizedError,
    ConcurrentModificationError,
    DuplicateReviewError,
    FormalAttemptNotFoundError,
    FormalReviewNotFoundError,
    InvalidReviewDecisionError,
    InvalidStateTransitionError,
    ReviewAlreadyDecidedError,
    ReviewQueueUnavailableError,
)
from app.modules.formal_assessment.domain.review import FormalReview, new_formal_review
from app.modules.formal_assessment.ids import IdGenerator
from app.modules.formal_assessment.integration.assessors import Assessor, AssessorDirectory
from app.modules.formal_assessment.integration.audit import FormalAuditLog, safe_record
from app.modules.formal_assessment.integration.notification import (
    LearnerNotification,
    LearnerNotificationEvent,
    LearnerNotifier,
)
from app.modules.formal_assessment.integration.profiles import LearnerProfileProvider
from app.modules.formal_assessment.integration.review_queue import (
    ReviewQueueEntry,
    ReviewQueuePublisher,
)
from app.modules.formal_assessment.integration.uc03 import AttemptProvider
from app.modules.formal_assessment.repositories.protocols import (
    DeviceSessionRepository,
    FormalAttemptRepository,
    FormalReviewRepository,
)

logger = get_logger(__name__)

#: Page size ceiling for the pending list, so a client cannot ask for the whole table.
MAX_PENDING_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class PendingReviewPage:
    """One page of the assessor's queue, plus the depth behind it."""

    reviews: tuple[FormalReview, ...]
    total_pending: int
    limit: int
    offset: int
    #: The scope the list was filtered to. ``None`` means a platform-wide assessor.
    course_ids: tuple[str, ...] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "reviews": [review.as_dict() for review in self.reviews],
            "total_pending": self.total_pending,
            "limit": self.limit,
            "offset": self.offset,
            "course_ids": list(self.course_ids) if self.course_ids is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ReviewDetail:
    """Everything an assessor needs to decide (§10)."""

    review: FormalReview
    formal_attempt: FormalAttempt
    learner: dict[str, Any]
    assessment: dict[str, Any]
    score: dict[str, Any] | None
    responses: tuple[dict[str, Any], ...]
    attempt: dict[str, Any] | None
    anomalies: tuple[dict[str, Any], ...]
    submission: dict[str, Any]
    disconnect: dict[str, Any] | None
    supervision: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "review": self.review.as_dict(),
            "formal_attempt": self.formal_attempt.as_dict(),
            "learner": self.learner,
            "assessment": self.assessment,
            "score": self.score,
            "responses": list(self.responses),
            "attempt": self.attempt,
            "anomalies": list(self.anomalies),
            "submission": self.submission,
            "disconnect": self.disconnect,
            "supervision": self.supervision,
        }


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    """The result of an assessor's decision, and what followed from it."""

    review: FormalReview
    formal_attempt: FormalAttempt
    #: Present when an approval triggered the certificate workflow. ``None`` when it did not —
    #: either
    #: because the decision was an escalation, or because the workflow could not be reached and the
    #: trigger is still outstanding.
    certificate: dict[str, Any] | None = None
    notification_delivered: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "review": self.review.as_dict(),
            "formal_attempt": self.formal_attempt.as_dict(),
            "certificate": self.certificate,
            "notification_delivered": self.notification_delivered,
        }


class FormalReviewService:
    """Creating, queuing, reading and deciding formal reviews.

    ``certificates`` is injected late (``bind_certificates``) because the certificate service needs
    this service's repository and this service needs the certificate service after an approval.
    Binding it in the composition root rather than constructing one inside the other keeps both
    testable in isolation and keeps the dependency visible in one file.
    """

    def __init__(
        self,
        *,
        reviews: FormalReviewRepository,
        attempts: FormalAttemptRepository,
        sessions: DeviceSessionRepository,
        upstream: AttemptProvider,
        profiles: LearnerProfileProvider,
        assessors: AssessorDirectory,
        queue: ReviewQueuePublisher,
        notifier: LearnerNotifier,
        audit: FormalAuditLog,
        clock: Clock,
        new_id: IdGenerator,
        max_publish_attempts: int,
    ) -> None:
        self._reviews = reviews
        self._attempts = attempts
        self._sessions = sessions
        self._upstream = upstream
        self._profiles = profiles
        self._assessors = assessors
        self._queue = queue
        self._notifier = notifier
        self._audit = audit
        self._clock = clock
        self._new_id = new_id
        self._max_publish_attempts = max_publish_attempts
        self._certificates: Any | None = None

    def bind_certificates(self, certificates: Any) -> None:
        """Wire in the certificate service. Called once, by the composition root."""
        self._certificates = certificates

    # ------------------------------------------------------------------
    # Creation and queueing (§9, §13)
    # ------------------------------------------------------------------

    async def create_pending(self, formal_attempt: FormalAttempt) -> FormalReview:
        """Create the PENDING_REVIEW record for a passing formal attempt (§9).

        Idempotent: an existing review for the attempt is returned rather than duplicated, and a
        concurrent creation that loses the uniqueness constraint reads the winner.
        """
        existing = await self._reviews.get_by_formal_attempt(formal_attempt.formal_attempt_id)
        if existing is not None:
            return existing

        # A pass implies a submitted attempt, so this is defensive rather than reachable.
        if formal_attempt.attempt_id is None:  # pragma: no cover
            raise InvalidStateTransitionError(
                formal_attempt_id=formal_attempt.formal_attempt_id,
                current_state=formal_attempt.state.value,
                target_state=FormalAttemptState.PENDING_REVIEW.value,
            )

        now = to_iso(self._clock.now())
        review = new_formal_review(
            review_id=self._new_id(),
            formal_attempt_id=formal_attempt.formal_attempt_id,
            learner_id=formal_attempt.learner_id,
            course_id=formal_attempt.course_id,
            quiz_id=formal_attempt.quiz_id,
            attempt_id=formal_attempt.attempt_id,
            now=now,
            percentage=formal_attempt.result.percentage if formal_attempt.result else None,
            submitted_at=formal_attempt.submitted_at,
            auto_submitted=formal_attempt.auto_submitted,
            anomaly_count=len(formal_attempt.anomalies),
        )
        try:
            return await self._reviews.insert(review)
        except DuplicateReviewError:
            winner = await self._reviews.get_by_formal_attempt(formal_attempt.formal_attempt_id)
            if winner is None:  # pragma: no cover - the constraint fired, so a record exists
                raise
            return winner

    async def publish(self, review: FormalReview) -> FormalReview:
        """Notify the assessor queue. **Never raises** (§13).

        A queue failure updates the review's publish state, emits a QUEUE_FAILURE audit event and
        returns. The pass workflow that called it carries on, because the thing that matters — a
        durable PENDING_REVIEW with a blocked certificate — already happened.
        """
        if review.publish_state.value == "PUBLISHED":
            return review

        entry = ReviewQueueEntry(
            review_id=review.review_id,
            formal_attempt_id=review.formal_attempt_id,
            learner_id=review.learner_id,
            course_id=review.course_id,
            quiz_id=review.quiz_id,
            attempt_id=review.attempt_id,
            created_at=review.created_at,
            percentage=review.percentage,
            auto_submitted=review.auto_submitted,
            anomaly_count=review.anomaly_count,
        )

        now = to_iso(self._clock.now())
        try:
            await self._queue.publish(entry)
        # Any adapter failure is transient as far as this method is concerned: the review is already
        # durable, and publishing is a notification.
        except Exception as error:  # noqa: BLE001
            failed = review.publish_failed(
                now=now, error=str(error), max_attempts=self._max_publish_attempts
            )
            stored = await self._save_review_quietly(failed)
            await safe_record(
                self._audit,
                FormalAuditEvent.QUEUE_FAILURE,
                review_id=review.review_id,
                formal_attempt_id=review.formal_attempt_id,
                learner_id=review.learner_id,
                course_id=review.course_id,
                publish_attempts=stored.publish_attempts,
                publish_state=stored.publish_state.value,
                error=type(error).__name__,
                recoverable=True,
            )
            return stored

        stored = await self._save_review_quietly(review.published(now=now))
        return stored

    # ------------------------------------------------------------------
    # Assessor reads (§10)
    # ------------------------------------------------------------------

    async def list_pending(
        self, *, assessor_id: str, limit: int = 50, offset: int = 0
    ) -> PendingReviewPage:
        """The assessor's queue, oldest first, scoped to the courses they may review (§9, §10)."""
        assessor = await self._require_assessor(assessor_id)
        scope = await self._scope_for(assessor)
        bounded = max(1, min(limit, MAX_PENDING_PAGE_SIZE))
        reviews = await self._reviews.list_pending(
            course_ids=scope, limit=bounded, offset=max(0, offset)
        )
        total = await self._reviews.count_pending(course_ids=scope)
        return PendingReviewPage(
            reviews=reviews,
            total_pending=total,
            limit=bounded,
            offset=max(0, offset),
            course_ids=scope,
        )

    async def get_detail(self, *, assessor_id: str, review_id: str) -> ReviewDetail:
        """Everything an assessor needs to decide (§10)."""
        review = await self._require_review(review_id)
        assessor = await self._require_assessor(assessor_id)
        self._require_scope(assessor, review.course_id)

        formal_attempt = await self._attempts.get(review.formal_attempt_id)
        # A review implies an attempt, and there is no delete anywhere in this module.
        if formal_attempt is None:  # pragma: no cover
            raise FormalAttemptNotFoundError(review.formal_attempt_id)

        learner: dict[str, Any] = {"learner_id": review.learner_id}
        # Best effort: an unreachable profile source must not stop an assessor reading the
        # assessment. The
        # identity confirmation on the record is the evidence that matters, and it is included
        # below.
        with contextlib.suppress(Exception):
            profile = await self._profiles.get_profile(review.learner_id)
            if profile is not None:
                learner = {
                    "learner_id": profile.learner_id,
                    "full_name": profile.full_name,
                    "email": profile.email,
                    "email_confirmed": profile.email_confirmed,
                }

        upstream_attempt = None
        with contextlib.suppress(Exception):
            attempt = await self._upstream.get_attempt(review.attempt_id)
            upstream_attempt = attempt.as_dict() if attempt else None

        responses: tuple[dict[str, Any], ...] = ()
        with contextlib.suppress(Exception):
            responses = tuple(
                item.as_dict()
                for item in await self._upstream.get_attempt_responses(review.attempt_id)
            )

        sessions = await self._sessions.list_for_attempt(review.formal_attempt_id)

        return ReviewDetail(
            review=review,
            formal_attempt=formal_attempt,
            learner=learner,
            assessment={
                "course_id": review.course_id,
                "quiz_id": review.quiz_id,
                "attempt_id": review.attempt_id,
                "attempt_number": formal_attempt.attempt_number,
                "configuration_version_id": formal_attempt.configuration_version_id,
                "conditions": formal_attempt.conditions.as_dict()
                if formal_attempt.conditions
                else None,
                "identity_confirmation": formal_attempt.identity.as_dict()
                if formal_attempt.identity
                else None,
            },
            score=formal_attempt.result.as_dict() if formal_attempt.result else None,
            responses=responses,
            attempt=upstream_attempt,
            anomalies=tuple(item.as_dict() for item in formal_attempt.anomalies),
            submission={
                "submitted_at": formal_attempt.submitted_at,
                "submission_reason": formal_attempt.submission_reason.value
                if formal_attempt.submission_reason
                else None,
                "auto_submitted": formal_attempt.auto_submitted,
            },
            disconnect=formal_attempt.disconnect.as_dict() if formal_attempt.disconnect else None,
            supervision={
                # The audit trail itself lives in the platform's audit pipeline — UC-09 writes to it
                # and
                # never reads it back. What is included here is the equivalent evidence held on
                # UC-09's own
                # records: which devices were involved, which were refused, and what was flagged.
                "device_sessions": [session.as_dict() for session in sessions],
                "rejected_device_count": sum(
                    1 for session in sessions if session.state.value == "REJECTED"
                ),
                "identity_rejected_attempts": (
                    formal_attempt.identity.rejected_attempts if formal_attempt.identity else 0
                ),
                "anomaly_count": len(formal_attempt.anomalies),
                "audit_reference": {
                    "formal_attempt_id": formal_attempt.formal_attempt_id,
                    "attempt_id": formal_attempt.attempt_id,
                    "note": (
                        "Full audit events for these identifiers are held in the platform audit "
                        "trail."
                    ),
                },
            },
        )

    # ------------------------------------------------------------------
    # Assessor decisions (§10, §20)
    # ------------------------------------------------------------------

    async def start_review(self, *, assessor_id: str, review_id: str) -> FormalReview:
        """Record that an assessor has opened the review (§10, §14)."""
        review = await self._require_review(review_id)
        assessor = await self._require_assessor(assessor_id)
        self._require_scope(assessor, review.course_id)

        now = to_iso(self._clock.now())
        started = review.start(assessor_id=assessor_id, now=now)
        try:
            stored = await self._reviews.save(started)
        except ConcurrentModificationError:
            fresh = await self._require_review(review_id)
            if fresh.decided:
                raise ReviewAlreadyDecidedError(
                    review_id=fresh.review_id,
                    state=fresh.state.value,
                    decided_by=fresh.decision.decided_by if fresh.decision else None,
                ) from None
            stored = await self._reviews.save(fresh.start(assessor_id=assessor_id, now=now))

        await safe_record(
            self._audit,
            FormalAuditEvent.ASSESSOR_REVIEW_STARTED,
            review_id=stored.review_id,
            formal_attempt_id=stored.formal_attempt_id,
            learner_id=stored.learner_id,
            course_id=stored.course_id,
            assessor_id=assessor_id,
            state=stored.state.value,
        )
        return stored

    async def decide(
        self,
        *,
        assessor_id: str,
        review_id: str,
        decision: str,
        notes: str | None = None,
    ) -> DecisionOutcome:
        """Record an assessor's decision and act on it (§10, §11, §12)."""
        parsed = parse_enum(AssessorDecision, decision)
        if parsed is None:
            raise InvalidReviewDecisionError(
                decision=str(decision),
                allowed=tuple(member.value for member in AssessorDecision),
            )

        review = await self._require_review(review_id)
        assessor = await self._require_assessor(assessor_id)
        self._require_scope(assessor, review.course_id)

        formal_attempt = await self._attempts.get(review.formal_attempt_id)
        if formal_attempt is None:  # pragma: no cover - there is no delete
            raise FormalAttemptNotFoundError(review.formal_attempt_id)

        now = to_iso(self._clock.now())
        decided = review.decide(decision=parsed, assessor_id=assessor_id, now=now, notes=notes)
        try:
            stored_review = await self._reviews.save(decided)
        except ConcurrentModificationError:
            # Two assessors decided at once. The first decision stands; the second is told what it
            # was.
            fresh = await self._require_review(review_id)
            raise ReviewAlreadyDecidedError(
                review_id=fresh.review_id,
                state=fresh.state.value,
                decided_by=fresh.decision.decided_by if fresh.decision else None,
            ) from None

        if parsed is AssessorDecision.APPROVED:
            stored_attempt = await self._attempts.save(formal_attempt.approve(now=now))
            await safe_record(
                self._audit,
                FormalAuditEvent.ASSESSOR_APPROVED,
                review_id=stored_review.review_id,
                formal_attempt_id=stored_attempt.formal_attempt_id,
                learner_id=stored_attempt.learner_id,
                course_id=stored_attempt.course_id,
                quiz_id=stored_attempt.quiz_id,
                attempt_id=stored_attempt.attempt_id,
                assessor_id=assessor_id,
                percentage=stored_review.percentage,
                state=stored_attempt.state.value,
            )
            return await self._after_approval(
                review=stored_review, formal_attempt=stored_attempt, assessor_id=assessor_id
            )

        stored_attempt = await self._attempts.save(formal_attempt.require_further_review(now=now))
        await safe_record(
            self._audit,
            FormalAuditEvent.REQUIRES_FURTHER_REVIEW,
            review_id=stored_review.review_id,
            formal_attempt_id=stored_attempt.formal_attempt_id,
            learner_id=stored_attempt.learner_id,
            course_id=stored_attempt.course_id,
            attempt_id=stored_attempt.attempt_id,
            assessor_id=assessor_id,
            state=stored_attempt.state.value,
        )
        delivered = await self._notify(
            formal_attempt=stored_attempt,
            review=stored_review,
            event=LearnerNotificationEvent.FORMAL_ASSESSMENT_REQUIRES_FURTHER_REVIEW,
        )
        return DecisionOutcome(
            review=stored_review,
            formal_attempt=stored_attempt,
            certificate=None,
            notification_delivered=delivered,
        )

    async def retry_publish(self, review_id: str) -> FormalReview:
        """Publish a review the queue has not accepted (§13).

        Unlike :meth:`publish`, this one raises when the queue is still unavailable: the caller
        asked specifically to publish, so it deserves to be told the queue is down rather than to
        see a success that did not happen. The review remains recoverable either way.
        """
        review = await self._require_review(review_id)
        if not review.awaiting_publish:
            return review

        await safe_record(
            self._audit,
            FormalAuditEvent.QUEUE_RETRY,
            review_id=review.review_id,
            formal_attempt_id=review.formal_attempt_id,
            course_id=review.course_id,
            publish_attempts=review.publish_attempts,
            publish_state=review.publish_state.value,
        )

        stored = await self.publish(review)
        if stored.awaiting_publish:
            raise ReviewQueueUnavailableError(
                "The assessor review queue could not accept this review. It remains pending and "
                "the certificate remains blocked."
            )
        return stored

    async def list_unpublished(self, *, limit: int = 100) -> tuple[FormalReview, ...]:
        """Reviews the queue has not accepted, for the recovery surface (§13)."""
        return await self._reviews.list_unpublished(limit=limit)

    async def get_for_formal_attempt(self, formal_attempt_id: str) -> FormalReview | None:
        return await self._reviews.get_by_formal_attempt(formal_attempt_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _after_approval(
        self, *, review: FormalReview, formal_attempt: FormalAttempt, assessor_id: str
    ) -> DecisionOutcome:
        """Trigger the certificate workflow and notify the learner (§11, §12).

        Both are best effort and neither can undo the approval. An approval that was persisted and a
        certificate workflow that could not be reached is a retriable trigger; an approval rolled
        back because a notification failed would be a data-integrity bug wearing a helpful face.
        """
        certificate: dict[str, Any] | None = None
        updated = formal_attempt

        if self._certificates is not None:
            try:
                outcome = await self._certificates.trigger(
                    formal_attempt=formal_attempt, review=review
                )
                certificate = outcome.as_dict()
                updated = outcome.formal_attempt
            except Exception as error:  # noqa: BLE001 - see the docstring
                logger.warning(
                    "formal.certificate.trigger_deferred",
                    extra={
                        "formal_attempt_id": formal_attempt.formal_attempt_id,
                        "review_id": review.review_id,
                        "error": type(error).__name__,
                    },
                )

        delivered = await self._notify(
            formal_attempt=updated,
            review=review,
            event=LearnerNotificationEvent.FORMAL_ASSESSMENT_APPROVED,
        )
        return DecisionOutcome(
            review=review,
            formal_attempt=updated,
            certificate=certificate,
            notification_delivered=delivered,
        )

    async def _notify(
        self,
        *,
        formal_attempt: FormalAttempt,
        review: FormalReview,
        event: LearnerNotificationEvent,
    ) -> bool | None:
        """Notify the learner (§12). Failure is audited and never propagated."""
        notification = LearnerNotification(
            event=event,
            learner_id=formal_attempt.learner_id,
            course_id=formal_attempt.course_id,
            quiz_id=formal_attempt.quiz_id,
            formal_attempt_id=formal_attempt.formal_attempt_id,
            attempt_id=formal_attempt.attempt_id,
            review_id=review.review_id,
            percentage=review.percentage,
            occurred_at=to_iso(self._clock.now()),
            context={"state": formal_attempt.state.value},
        )
        try:
            outcome = await self._notifier.notify(notification)
        except Exception as error:  # noqa: BLE001 - a notification cannot corrupt an assessment
            await safe_record(
                self._audit,
                FormalAuditEvent.NOTIFICATION_FAILED,
                formal_attempt_id=formal_attempt.formal_attempt_id,
                review_id=review.review_id,
                learner_id=formal_attempt.learner_id,
                notification_event=event.value,
                error=type(error).__name__,
            )
            return False

        if not outcome.delivered:
            await safe_record(
                self._audit,
                FormalAuditEvent.NOTIFICATION_FAILED,
                formal_attempt_id=formal_attempt.formal_attempt_id,
                review_id=review.review_id,
                learner_id=formal_attempt.learner_id,
                notification_event=event.value,
                error=outcome.error,
            )
            return False

        await safe_record(
            self._audit,
            FormalAuditEvent.LEARNER_NOTIFIED,
            formal_attempt_id=formal_attempt.formal_attempt_id,
            review_id=review.review_id,
            learner_id=formal_attempt.learner_id,
            notification_event=event.value,
            reference=outcome.reference,
        )
        return True

    async def _require_review(self, review_id: str) -> FormalReview:
        review = await self._reviews.get(review_id)
        if review is None:
            raise FormalReviewNotFoundError(review_id)
        return review

    async def _require_assessor(self, assessor_id: str) -> Assessor:
        """Authorisation, checked on every operation (§10, §19).

        An unknown assessor and an inactive one produce the same refusal as an out-of-scope one:
        telling a caller which of the three applies would let them enumerate the assessor register.
        """
        assessor = await self._assessors.get_assessor(assessor_id)
        if assessor is None or not assessor.active:
            raise AssessorNotAuthorizedError(assessor_id=assessor_id)
        return assessor

    def _require_scope(self, assessor: Assessor, course_id: str) -> None:
        if not assessor.may_review(course_id):
            raise AssessorNotAuthorizedError(
                assessor_id=assessor.assessor_id, course_id=course_id
            )

    async def _scope_for(self, assessor: Assessor) -> tuple[str, ...] | None:
        """The course filter for this assessor's queue.

        ``None`` — unrestricted — is returned **only** for an assessor explicitly marked
        ``all_courses``. Everyone else gets their course list, and an empty list means an empty
        queue rather than the whole table.
        """
        if assessor.all_courses:
            return None
        directory_scope = await self._assessors.list_authorised_course_ids(assessor.assessor_id)
        return tuple(directory_scope or tuple(sorted(assessor.authorised_course_ids)))

    async def _save_review_quietly(self, review: FormalReview) -> FormalReview:
        """Persist a publish-state change without letting it fail the caller (§13)."""
        try:
            return await self._reviews.save(review)
        except ConcurrentModificationError:
            fresh = await self._reviews.get(review.review_id)
            return fresh or review
        except Exception:  # noqa: BLE001 - a publish bookkeeping failure must not fail a pass
            logger.warning(
                "formal.review.publish_state_not_recorded",
                extra={"review_id": review.review_id},
            )
            return review
