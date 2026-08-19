"""Attempt lookup, ownership and lifecycle gating.

Every request that touches an attempt goes through here, which is what makes three
guarantees hold uniformly instead of being re-implemented per endpoint:

1. **Ownership** — an attempt is only ever visible to the learner who owns it.
2. **Expiry is enforced on access** — if a timed attempt's deadline has passed, it is
   submitted from the latest saved answers *before* the request is answered. The
   server therefore never serves, or accepts writes against, an attempt that has
   silently run out of time — no background job required.
3. **Locked attempts reject writes** — a submitted or pending attempt is immutable for
   the learner.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from app.core.time import iso_or_none
from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.domain.enums import AttemptStatus
from app.modules.attempt_delivery.models import QuizAttempt
from app.modules.attempt_delivery.repositories.attempt_repository import AttemptRepository
from app.modules.attempt_delivery.services.submission_service import SubmissionService
from app.modules.attempt_delivery.services.timing_service import TimingService


@dataclass(frozen=True, slots=True)
class AccessOutcome:
    attempt: QuizAttempt
    #: True when this request triggered the automatic time-expiry submission.
    auto_submitted: bool


class AttemptAccessService:
    """Loads attempts, settling expiry and enforcing the lifecycle."""

    __slots__ = ("_attempts", "_submissions", "_timing")

    def __init__(
        self,
        *,
        attempts: AttemptRepository,
        submissions: SubmissionService,
        timing: TimingService,
    ) -> None:
        self._attempts = attempts
        self._submissions = submissions
        self._timing = timing

    def _require(self, attempt_id: str, learner_id: str) -> QuizAttempt:
        attempt = self._attempts.get_for_learner(attempt_id, learner_id)
        # Deliberately a 404 rather than a 403: another learner's attempt must not be
        # distinguishable from one that does not exist.
        if attempt is None:
            raise errors.attempt_not_found(attempt_id)
        return attempt

    def load(self, attempt_id: str, learner_id: str) -> AccessOutcome:
        """Load an attempt, first settling any elapsed time limit.

        Reads never fail because of expiry — they return the (now submitted) attempt,
        so a client reconnecting after the deadline sees the authoritative final state
        rather than an error.
        """
        return self._settle_expiry(self._require(attempt_id, learner_id))

    def load_for_write(self, attempt_id: str, learner_id: str) -> QuizAttempt:
        """Load an attempt that is about to be modified.

        Rejects anything not ACTIVE, and treats an elapsed time limit as a rejection
        *after* auto-submitting, so a write arriving even a moment late cannot land on
        a finished attempt.
        """
        outcome = self.load(attempt_id, learner_id)
        self.assert_writable(outcome.attempt)
        return outcome.attempt

    def load_for_submission(
        self, attempt_id: str, learner_id: str, *, allow_pending: bool = False
    ) -> QuizAttempt:
        """Load an attempt for submission.

        Unlike :meth:`load_for_write` this tolerates SUBMISSION_PENDING when
        ``allow_pending`` is set, because retrying a pending submission is exactly what
        a learner in that state needs to do.
        """
        outcome = self.load(attempt_id, learner_id)
        attempt = outcome.attempt
        if attempt.status == str(AttemptStatus.SUBMISSION_PENDING) and not allow_pending:
            raise errors.attempt_submission_pending(attempt.id)
        return attempt

    def assert_writable(self, attempt: QuizAttempt) -> None:
        """Raise unless the attempt currently accepts learner modifications."""
        if attempt.status == str(AttemptStatus.SUBMITTED):
            raise errors.attempt_already_submitted(attempt.id, iso_or_none(attempt.submitted_at))
        if attempt.status == str(AttemptStatus.SUBMISSION_PENDING):
            raise errors.attempt_submission_pending(attempt.id)
        # Belt and braces: `_settle_expiry` should already have converted this, but a
        # write must never slip through on a stale in-memory attempt.
        if self._timing.is_expired(attempt):
            raise errors.attempt_expired(attempt.id, iso_or_none(attempt.expires_at))

    def _settle_expiry(self, attempt: QuizAttempt) -> AccessOutcome:
        """Submit the attempt if its time limit has elapsed.

        The submission uses the latest successfully persisted answers, which is why
        autosave and expiry compose safely: whatever reached the database is what gets
        submitted, and nothing is invented at expiry time.
        """
        if attempt.status != str(AttemptStatus.ACTIVE):
            return AccessOutcome(attempt=attempt, auto_submitted=False)
        if not self._timing.is_expired(attempt):
            return AccessOutcome(attempt=attempt, auto_submitted=False)

        # A downstream hand-off failure must not stop the caller from seeing the attempt's real
        # state: the local commit is durable and the submission is left PENDING for retry.
        with suppress(errors.AppError):
            self._submissions.submit_on_expiry(attempt)

        settled = self._attempts.get(attempt.id)
        return AccessOutcome(attempt=settled or attempt, auto_submitted=True)
