"""UC-09's own failure taxonomy (§18).

Every failure that is part of normal operation is one of these, so the API layer can render it
directly and a caller can branch on a stable ``code``. Anything else becomes a generic
``INTERNAL_ERROR`` in ``app.core.exception_handlers`` — driver messages, SQL and tracebacks never
reach a client.

Codes overlap deliberately with the sibling use cases where they mean the same thing
(``ATTEMPT_NOT_FOUND``, ``ATTEMPT_ALREADY_SUBMITTED``, ``INVALID_STATE_TRANSITION``), so a client
that already handles a UC-03 or UC-08 refusal handles the UC-09 one without new branches.

HTTP STATUS CHOICES
-------------------
``403`` for the three security refusals — AI coaching during a formal assessment, an unauthorised
assessor, a certificate without approval. They are not "you sent something malformed" and not "the
resource is busy"; they are "you are not permitted to do this", and the status has to say so because
the platform's monitoring will be watching for exactly that.

``409`` for everything that is a conflict with the current state of a formal attempt: conditions not
acknowledged, a second device, a pause, a resume, a duplicate submission. ``422`` for well-formed
requests the domain cannot act on: a name that does not match, an unrecognised decision. ``503``,
retryable, for the queue and for upstream modules that could not be reached — nothing has been
decided, so repeating the request is safe.

WHAT AN ERROR NEVER CARRIES
---------------------------
``IdentityMismatchError`` does not echo the profile's name or email address, and does not say which
character differed. It says which field failed. A confirmation screen that could be used to
enumerate a learner's registered email address would be a worse problem than the one it solves.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import (
    ConflictError,
    FieldIssue,
    ForbiddenError,
    NotFoundError,
    ProviderUnavailableError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Lookup and ownership
# ---------------------------------------------------------------------------


class LearnerProfileNotFoundError(NotFoundError):
    def __init__(self, learner_id: str) -> None:
        super().__init__("Learner profile", learner_id, code="LEARNER_PROFILE_NOT_FOUND")


class QuizNotFoundError(NotFoundError):
    def __init__(self, quiz_id: str) -> None:
        super().__init__("Quiz", quiz_id, code="QUIZ_NOT_FOUND")


class AttemptNotFoundError(NotFoundError):
    def __init__(self, attempt_id: str) -> None:
        super().__init__("Attempt", attempt_id, code="ATTEMPT_NOT_FOUND")


class FormalAttemptNotFoundError(NotFoundError):
    def __init__(self, formal_attempt_id: str) -> None:
        super().__init__("Formal attempt", formal_attempt_id, code="FORMAL_ATTEMPT_NOT_FOUND")


class FormalReviewNotFoundError(NotFoundError):
    def __init__(self, review_id: str) -> None:
        super().__init__("Formal review", review_id, code="FORMAL_REVIEW_NOT_FOUND")


class FormalAttemptOwnershipError(ForbiddenError):
    """The formal attempt exists but belongs to another learner.

    Refused before any formal logic runs, so a guessed id can never become an operation on someone
    else's assessment.
    """

    code = "FORMAL_ATTEMPT_NOT_OWNED"

    def __init__(self, formal_attempt_id: str, learner_id: str) -> None:
        super().__init__(
            "This formal attempt does not belong to the requesting learner.",
            context={"formal_attempt_id": formal_attempt_id, "learner_id": learner_id},
        )


class QuizNotFormalAssessmentError(ValidationError):
    """The quiz is not configured as a formal assessment, so UC-09 has nothing to gate.

    A refusal rather than a silent pass-through: a client that asked UC-09 to start a formal attempt
    at an ordinary quiz has a bug, and starting an unsupervised attempt in response would hide it.
    """

    def __init__(self, quiz_id: str) -> None:
        super().__init__(
            "This quiz is not configured as a formal assessment.",
            code="QUIZ_NOT_FORMAL_ASSESSMENT",
            context={"quiz_id": quiz_id},
        )


# ---------------------------------------------------------------------------
# Conditions and identity (§1, §2)
# ---------------------------------------------------------------------------


class ConditionsNotAcknowledgedError(ConflictError):
    """The formal conditions have not been acknowledged, so nothing may start (§1)."""

    def __init__(
        self,
        *,
        learner_id: str,
        quiz_id: str,
        required_version: str,
        acknowledged_version: str | None = None,
    ) -> None:
        message = (
            "The formal assessment conditions must be acknowledged before the attempt can start."
            if acknowledged_version is None
            else (
                "The acknowledged formal assessment conditions are out of date and must be "
                "acknowledged again."
            )
        )
        super().__init__(
            message,
            code="CONDITIONS_NOT_ACKNOWLEDGED",
            context={
                "learner_id": learner_id,
                "quiz_id": quiz_id,
                "required_conditions_version": required_version,
                "acknowledged_conditions_version": acknowledged_version,
            },
        )


class ConditionsAcknowledgementIncompleteError(ValidationError):
    """The learner acknowledged some conditions but not all of them (§1).

    ``conditions_acknowledged == true`` is a single boolean in the specification; this is what
    enforces that the boolean cannot be true while a condition is unticked.
    """

    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__(
            "Every formal assessment condition must be acknowledged.",
            code="CONDITIONS_ACKNOWLEDGEMENT_INCOMPLETE",
            context={"missing_conditions": list(missing)},
        )


class IdentityNotConfirmedError(ConflictError):
    """Identity confirmation has not happened, so the attempt may not start (§2)."""

    def __init__(self, *, learner_id: str, quiz_id: str) -> None:
        super().__init__(
            "Identity must be confirmed before a formal assessment can start.",
            code="IDENTITY_NOT_CONFIRMED",
            context={"learner_id": learner_id, "quiz_id": quiz_id},
        )


class IdentityMismatchError(ValidationError):
    """The entered name did not match the learner's profile name (§2).

    Carries the failing field and nothing else — see the module docstring.
    """

    def __init__(self, fields: tuple[str, ...]) -> None:
        super().__init__(
            "The details entered do not match the learner's profile.",
            code="IDENTITY_MISMATCH",
            context={"mismatched_fields": list(fields)},
        )
        self.details = [
            FieldIssue(
                field=field.lower(),
                code="IDENTITY_MISMATCH",
                message="This value does not match the learner's profile.",
            )
            for field in fields
        ]


class EmailNotConfirmedError(ConflictError):
    """The learner's email address has not been confirmed (§2).

    A state of the learner's account, not of the request: no request field can satisfy it, which is
    why it is a conflict rather than a validation failure.
    """

    def __init__(self, learner_id: str) -> None:
        super().__init__(
            "The learner's email address must be confirmed before a formal assessment can start.",
            code="EMAIL_NOT_CONFIRMED",
            context={"learner_id": learner_id},
        )


class LearnerProfileUnavailableError(ProviderUnavailableError):
    """The profile source could not be read, so identity cannot be confirmed.

    Retryable, and deliberately *not* degradable: "we could not check the learner's name" must never
    become "the name is fine".
    """

    code = "LEARNER_PROFILE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Starting, sessions and the device lock (§3)
# ---------------------------------------------------------------------------


class FormalAttemptAlreadyStartedError(ConflictError):
    """A formal attempt at this quiz is already under way for this learner."""

    def __init__(self, *, formal_attempt_id: str, state: str) -> None:
        super().__init__(
            "A formal assessment for this quiz has already started.",
            code="FORMAL_ATTEMPT_ALREADY_STARTED",
            context={"formal_attempt_id": formal_attempt_id, "state": state},
        )


class SecondDeviceRejectedError(ConflictError):
    """Another device already holds the authoritative session (§3).

    The message is written to be shown to a learner: it tells them what to do (go back to the first
    device) rather than describing a lock.
    """

    def __init__(self, *, formal_attempt_id: str, active_session_id: str | None = None) -> None:
        super().__init__(
            "This formal assessment is already in progress on another device. Continue on that "
            "device to finish the assessment.",
            code="SECOND_DEVICE_REJECTED",
            context={
                "formal_attempt_id": formal_attempt_id,
                "active_session_id": active_session_id,
            },
        )


class DeviceSessionConflictError(ConflictError):
    """The session presented is not the authoritative session for this formal attempt.

    Distinct from :class:`SecondDeviceRejectedError`: that one is raised when a device tries to
    *take* the lock, this one when a device tries to *act* without holding it — an autosave or a
    submit from a session that was superseded or closed.
    """

    def __init__(self, *, formal_attempt_id: str, session_state: str | None = None) -> None:
        super().__init__(
            "This device does not hold the active session for the formal assessment.",
            code="DEVICE_SESSION_CONFLICT",
            context={"formal_attempt_id": formal_attempt_id, "session_state": session_state},
        )


class FormalAttemptNotActiveError(ConflictError):
    """The operation needs an attempt that is being sat, and this one is not."""

    def __init__(self, *, formal_attempt_id: str, state: str) -> None:
        super().__init__(
            "This formal assessment is not in progress.",
            code="FORMAL_ATTEMPT_NOT_ACTIVE",
            context={"formal_attempt_id": formal_attempt_id, "state": state},
        )


# ---------------------------------------------------------------------------
# Pause and resume (§4)
# ---------------------------------------------------------------------------


class PauseNotAllowedError(ConflictError):
    """Formal assessments cannot be paused (§4). There is no state to pause into."""

    def __init__(self, *, formal_attempt_id: str, state: str) -> None:
        super().__init__(
            "A formal assessment cannot be paused.",
            code="PAUSE_NOT_ALLOWED",
            context={"formal_attempt_id": formal_attempt_id, "state": state},
        )


class ResumeNotAllowedError(ConflictError):
    """A formal attempt cannot be resumed once it has ended (§4, §5).

    The submitted case and the auto-submitted case are the same refusal on purpose: from the
    learner's side, "you cannot go back in" is one fact, and the reason it happened is on the record
    and in the audit trail rather than in the error.
    """

    def __init__(self, *, formal_attempt_id: str, state: str) -> None:
        super().__init__(
            "A formal assessment cannot be resumed.",
            code="RESUME_NOT_ALLOWED",
            context={"formal_attempt_id": formal_attempt_id, "state": state},
        )


# ---------------------------------------------------------------------------
# Submission and disconnect (§5, §20)
# ---------------------------------------------------------------------------


class FormalAttemptAlreadySubmittedError(ConflictError):
    """The attempt is already submitted and its answers are immutable."""

    def __init__(self, *, formal_attempt_id: str, submitted_at: str | None = None) -> None:
        super().__init__(
            "This formal assessment has already been submitted.",
            code="FORMAL_ATTEMPT_ALREADY_SUBMITTED",
            context={"formal_attempt_id": formal_attempt_id, "submitted_at": submitted_at},
        )


class DuplicateSubmissionError(ConflictError):
    """Two submissions raced and this one lost.

    Not an error the learner ever needs to see — the services resolve a duplicate submission into a
    replay of the winner. It exists for the case where the winner cannot be read back, because
    reporting a conflict is safer than submitting twice.
    """

    def __init__(self, *, formal_attempt_id: str) -> None:
        super().__init__(
            "A submission for this formal assessment is already being processed.",
            code="DUPLICATE_SUBMISSION",
            context={"formal_attempt_id": formal_attempt_id},
            retryable=True,
        )


class DisconnectSubmissionConflictError(ConflictError):
    """A disconnect auto-submission and another submission collided.

    Raised only when the collision cannot be resolved by reading the winner. The learner's answers
    are safe either way: whichever submission won is the one that was built from the autosaved
    state.
    """

    def __init__(self, *, formal_attempt_id: str, state: str) -> None:
        super().__init__(
            "The formal assessment was submitted by another process while the disconnect was "
            "being handled.",
            code="DISCONNECT_SUBMISSION_CONFLICT",
            context={"formal_attempt_id": formal_attempt_id, "state": state},
            retryable=True,
        )


class AutoSubmitFailedError(ProviderUnavailableError):
    """UC-03 could not commit the auto-submission.

    Retryable, and the claim stays on the record: the attempt is AUTO_SUBMIT_IN_PROGRESS, cannot be
    resumed, and the next disconnect event or recovery sweep completes the submission. Nothing is
    lost, and nothing is submitted twice.
    """

    code = "AUTO_SUBMIT_FAILED"


class SubmissionFailedError(ProviderUnavailableError):
    """UC-03 could not commit the learner's submission. Retryable; the attempt stays active."""

    code = "FORMAL_SUBMISSION_FAILED"


# ---------------------------------------------------------------------------
# AI coaching (§7)
# ---------------------------------------------------------------------------


class AiCoachingForbiddenError(ForbiddenError):
    """AI coaching is not available during a formal assessment (§7).

    The error a direct call to the coaching API receives. Whether a button was rendered is beside
    the point: this is raised by the backend check, so the restriction holds for any caller.
    """

    code = "AI_COACHING_FORBIDDEN"

    def __init__(
        self, *, learner_id: str, reason: str, formal_attempt_id: str | None = None
    ) -> None:
        super().__init__(
            "AI coaching is not available while a formal assessment is in progress.",
            context={
                "learner_id": learner_id,
                "reason": reason,
                "formal_attempt_id": formal_attempt_id,
            },
        )


# ---------------------------------------------------------------------------
# Review and assessors (§9, §10)
# ---------------------------------------------------------------------------


class AssessorNotAuthorizedError(ForbiddenError):
    """The caller is not an assessor, or not one authorised for this course (§10).

    Two failures, one error, because distinguishing them would tell an unauthorised caller which
    courses exist and who reviews them.
    """

    code = "ASSESSOR_NOT_AUTHORIZED"

    def __init__(self, *, assessor_id: str, course_id: str | None = None) -> None:
        super().__init__(
            "This assessor is not authorised to review this formal assessment.",
            context={"assessor_id": assessor_id, "course_id": course_id},
        )


class InvalidReviewDecisionError(ValidationError):
    """The decision is not one UC-09 recognises (§10)."""

    def __init__(self, *, decision: str, allowed: tuple[str, ...]) -> None:
        super().__init__(
            "The review decision is not recognised.",
            code="INVALID_REVIEW_DECISION",
            context={"decision": decision, "allowed_decisions": list(allowed)},
        )


class ReviewAlreadyDecidedError(ConflictError):
    """The review already has a decision, and a decision is final (§20).

    The assessor-decision race resolves here: the first decision stands, the second is told what the
    first one was rather than overwriting it.
    """

    def __init__(self, *, review_id: str, state: str, decided_by: str | None = None) -> None:
        super().__init__(
            "This formal assessment has already been reviewed.",
            code="REVIEW_ALREADY_DECIDED",
            context={"review_id": review_id, "state": state, "decided_by": decided_by},
        )


class ReviewQueueUnavailableError(ProviderUnavailableError):
    """The assessor review queue could not be reached (§13).

    Never raised out of the pass workflow. The PENDING_REVIEW record is persisted before the queue
    is touched, so a queue outage leaves a recoverable review and a blocked certificate. This error
    surfaces only from an explicit retry, where the caller asked to publish and deserves to be told
    it did not work.
    """

    code = "REVIEW_QUEUE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Certificate gate (§11)
# ---------------------------------------------------------------------------


class CertificateNotApprovedError(ForbiddenError):
    """A certificate was requested for a formal assessment without assessor approval (§11).

    The single most important refusal in this module. It is raised by the gate that a certificate
    service must pass through, so a direct call to a certificate endpoint hits it too.
    """

    code = "CERTIFICATE_NOT_APPROVED"

    def __init__(
        self,
        *,
        formal_attempt_id: str,
        state: str,
        reason: str,
        review_id: str | None = None,
    ) -> None:
        super().__init__(
            "A certificate cannot be issued for this formal assessment until an authorised "
            "assessor has approved it.",
            context={
                "formal_attempt_id": formal_attempt_id,
                "state": state,
                "reason": reason,
                "review_id": review_id,
            },
        )


class CertificateWorkflowFailedError(ProviderUnavailableError):
    """The certificate workflow could not be triggered.

    Retryable. The approval stands: an approved formal assessment stays approved regardless of
    whether the certificate service was reachable, and the trigger can be repeated.
    """

    code = "CERTIFICATE_WORKFLOW_FAILED"


# ---------------------------------------------------------------------------
# State machine and persistence (§15, §20)
# ---------------------------------------------------------------------------


class InvalidStateTransitionError(ConflictError):
    """The requested move is not one the formal lifecycle permits (§15).

    The catch-all that makes the state machine load-bearing rather than advisory: anything not in
    the transition table arrives here, including transitions nobody anticipated.
    """

    def __init__(
        self,
        *,
        formal_attempt_id: str | None,
        current_state: str,
        target_state: str,
        allowed: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            f"A formal assessment cannot move from {current_state} to {target_state}.",
            code="INVALID_STATE_TRANSITION",
            context={
                "formal_attempt_id": formal_attempt_id,
                "current_state": current_state,
                "target_state": target_state,
                "allowed_target_states": list(allowed),
            },
        )


class ConcurrentModificationError(ConflictError):
    """The record changed underneath this write (§20).

    Raised by a repository whose compare-and-set failed. Services catch it, re-read and either
    replay the winner's outcome or refuse — they never retry blindly, because a blind retry is how
    one operation becomes two.
    """

    def __init__(self, *, record: str, identifier: str) -> None:
        super().__init__(
            "The record was modified by another request. Re-read it and try again.",
            code="CONCURRENT_MODIFICATION",
            context={"record": record, "identifier": identifier},
            retryable=True,
        )


class DuplicateFormalAttemptError(ConflictError):
    """The persistence layer refused a second formal attempt for the same natural key."""

    def __init__(self, *, learner_id: str, quiz_id: str, existing_id: str | None = None) -> None:
        super().__init__(
            "A formal assessment record already exists for this learner and quiz.",
            code="DUPLICATE_FORMAL_ATTEMPT",
            context={"learner_id": learner_id, "quiz_id": quiz_id, "existing_id": existing_id},
        )


class DuplicateReviewError(ConflictError):
    """The persistence layer refused a second review for one formal attempt (§20)."""

    def __init__(self, *, formal_attempt_id: str, existing_review_id: str | None = None) -> None:
        super().__init__(
            "A review already exists for this formal assessment.",
            code="DUPLICATE_REVIEW",
            context={
                "formal_attempt_id": formal_attempt_id,
                "existing_review_id": existing_review_id,
            },
        )


class DeviceSessionAlreadyHeldError(ConflictError):
    """The persistence layer refused a second active session for one formal attempt (§3).

    The raw constraint violation. ``DeviceSessionService`` turns it into
    :class:`SecondDeviceRejectedError` after recording the rejected device, so the learner sees a
    sentence about their other device rather than a uniqueness failure.
    """

    def __init__(self, *, formal_attempt_id: str, active_session_id: str | None = None) -> None:
        super().__init__(
            "An active device session already exists for this formal assessment.",
            code="DEVICE_SESSION_ALREADY_HELD",
            context={
                "formal_attempt_id": formal_attempt_id,
                "active_session_id": active_session_id,
            },
        )


class FormalAttemptCreationFailedError(ConflictError):
    """UC-03 refused to create the attempt, by its own rules.

    Added at integration, when UC-03 stopped being a port with a fake behind it and became a
    module that can genuinely refuse: the learner is not enrolled, the quiz was withdrawn, they
    already have an attempt open, or they have no attempts left. Kept distinct from
    :class:`AttemptDeliveryUnavailableError`, which means UC-03 did not *answer* — that one is
    retryable and implies nothing was written; this one needs the learner told what is wrong.

    UC-03's own code travels in the context rather than being discarded, so a client that already
    renders ``LEARNER_NOT_ENROLLED`` still can.
    """

    status_code = 409
    code = "FORMAL_ATTEMPT_CREATION_FAILED"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(
            message,
            code="FORMAL_ATTEMPT_CREATION_FAILED",
            context=context or None,
        )


class AttemptDeliveryUnavailableError(ProviderUnavailableError):
    """UC-03 could not be reached to deliver or read the attempt."""

    code = "ATTEMPT_DELIVERY_UNAVAILABLE"


class ScoringUnavailableError(ProviderUnavailableError):
    """UC-04 / UC-05 could not be reached, so no formal result can be recorded yet."""

    code = "RESULT_SOURCE_UNAVAILABLE"


def error_context(error: Any) -> dict[str, Any]:
    """The client-safe context of an error, for logging and for audit fields."""
    return dict(getattr(error, "context", {}) or {})
