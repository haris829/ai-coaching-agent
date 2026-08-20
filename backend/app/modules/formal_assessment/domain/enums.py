"""Enumerations owned by UC-09.

``StrEnum`` members compare equal to their string value, so they serialise straight into JSON and
into the ``String`` columns the company adapter will map them onto. String columns with ``CHECK``
constraints are preferred over native database enum types for the same reason UC-03 and UC-08 give:
they are portable and can be extended without a type migration.

WHAT IS *NOT* REDEFINED HERE
----------------------------
``AttemptStatus`` (ACTIVE / SUBMISSION_PENDING / SUBMITTED) belongs to UC-03. ``ResultStatus``
(PENDING / PASSED / FAILED) and the certificate lifecycle belong to UC-05. Question types belong to
UC-02. None of them is redeclared: they are consumed through the ports in ``integration``, and
UC-09's own lifecycle in :class:`FormalAttemptState` *wraps* UC-03's rather than replacing it. The
mapping is one-directional and explicit — see :data:`UC03_STATUS_FOR_FORMAL_STATE` — so there is one
attempt with one status upstream and one supervision record around it here, never two competing
state machines for the same thing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

_EnumT = TypeVar("_EnumT", bound=StrEnum)


class FormalAttemptState(StrEnum):
    """The supervision lifecycle of one formal attempt.

    Read top to bottom, this is the sequence the specification describes::

        NOT_STARTED -> CONDITIONS_ACKNOWLEDGED -> IDENTITY_CONFIRMED -> ACTIVE
            -> SUBMITTED -> RESULT_CALCULATED -> PASSED -> PENDING_REVIEW -> APPROVED
            -> CERTIFICATE_ALLOWED

    with three branches: ``RESULT_CALCULATED -> FAILED``, ``PENDING_REVIEW ->
    REQUIRES_FURTHER_REVIEW``, and the disconnect path ``ACTIVE -> AUTO_SUBMIT_IN_PROGRESS ->
    SUBMITTED``.

    ``PASSED``/``FAILED`` rather than ``PASS``/``FAIL`` because that is UC-05's existing vocabulary
    for the same fact, and a formal pass is the same kind of fact as any other pass.
    """

    #: No formal attempt record exists. Never stored — the absence of a record *is* this state, and
    #: it is named so the transition table has an origin and the API can report it.
    NOT_STARTED = "NOT_STARTED"
    #: The learner has acknowledged the formal conditions for a specific conditions version.
    CONDITIONS_ACKNOWLEDGED = "CONDITIONS_ACKNOWLEDGED"
    #: Name and confirmed email matched the profile. The attempt may now be started.
    IDENTITY_CONFIRMED = "IDENTITY_CONFIRMED"
    #: UC-03 has delivered the attempt and one device session holds the lock.
    ACTIVE = "ACTIVE"
    #: A disconnect was detected and this process has claimed the auto-submission. A durable claim
    #: rather than an in-memory flag, which is what makes repeated disconnect events idempotent
    #: across processes.
    AUTO_SUBMIT_IN_PROGRESS = "AUTO_SUBMIT_IN_PROGRESS"
    #: The attempt is submitted and immutable. Reached by the learner submitting or by auto-submit.
    SUBMITTED = "SUBMITTED"
    #: UC-04's score is confirmed and recorded here. The pass/fail branch happens next.
    RESULT_CALCULATED = "RESULT_CALCULATED"
    #: A passing formal result. Deliberately *not* the end of the road: a certificate is not owed
    #: yet, which is the whole point of UC-09.
    PASSED = "PASSED"
    #: A failing formal result. Terminal. No review, no certificate, nothing pending.
    FAILED = "FAILED"
    #: The pass is waiting for a human assessor. Recoverable: it survives a queue outage because the
    #: state is persisted before anything is published.
    PENDING_REVIEW = "PENDING_REVIEW"
    #: An authorised assessor approved the pass. Only now may a certificate be generated.
    APPROVED = "APPROVED"
    #: The assessor escalated instead of approving. The certificate stays blocked.
    REQUIRES_FURTHER_REVIEW = "REQUIRES_FURTHER_REVIEW"
    #: The certificate workflow has been triggered for an approved pass.
    CERTIFICATE_ALLOWED = "CERTIFICATE_ALLOWED"


#: States in which the learner is inside a formal assessment: a second device is refused, AI
#: coaching is refused, and no other formal attempt at the same quiz may be started.
OPEN_FORMAL_STATES: frozenset[FormalAttemptState] = frozenset(
    {
        FormalAttemptState.CONDITIONS_ACKNOWLEDGED,
        FormalAttemptState.IDENTITY_CONFIRMED,
        FormalAttemptState.ACTIVE,
        FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS,
    }
)

#: States in which the quiz is genuinely being sat. The AI-coaching restriction is scoped to these:
#: a learner who has acknowledged the conditions but not started is not yet in an assessment, and a
#: learner whose attempt is submitted is out of it.
IN_PROGRESS_FORMAL_STATES: frozenset[FormalAttemptState] = frozenset(
    {FormalAttemptState.ACTIVE, FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS}
)

#: States reached after the attempt is committed. Answers can never change again from here.
SUBMITTED_FORMAL_STATES: frozenset[FormalAttemptState] = frozenset(
    {
        FormalAttemptState.SUBMITTED,
        FormalAttemptState.RESULT_CALCULATED,
        FormalAttemptState.PASSED,
        FormalAttemptState.FAILED,
        FormalAttemptState.PENDING_REVIEW,
        FormalAttemptState.APPROVED,
        FormalAttemptState.REQUIRES_FURTHER_REVIEW,
        FormalAttemptState.CERTIFICATE_ALLOWED,
    }
)

#: The only states in which a certificate may exist. Everything else is a refusal.
CERTIFICATE_ALLOWED_STATES: frozenset[FormalAttemptState] = frozenset(
    {FormalAttemptState.APPROVED, FormalAttemptState.CERTIFICATE_ALLOWED}
)

#: How a UC-09 state maps onto the UC-03 attempt status it implies. Documentation with teeth: the
#: reconciliation check in ``services.formal_attempt_service`` uses it to notice a formal record and
#: an upstream attempt that have drifted apart, rather than trusting either silently.
UC03_STATUS_FOR_FORMAL_STATE: dict[FormalAttemptState, str | None] = {
    FormalAttemptState.NOT_STARTED: None,
    FormalAttemptState.CONDITIONS_ACKNOWLEDGED: None,
    FormalAttemptState.IDENTITY_CONFIRMED: None,
    FormalAttemptState.ACTIVE: "ACTIVE",
    FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS: "ACTIVE",
    FormalAttemptState.SUBMITTED: "SUBMITTED",
    FormalAttemptState.RESULT_CALCULATED: "SUBMITTED",
    FormalAttemptState.PASSED: "SUBMITTED",
    FormalAttemptState.FAILED: "SUBMITTED",
    FormalAttemptState.PENDING_REVIEW: "SUBMITTED",
    FormalAttemptState.APPROVED: "SUBMITTED",
    FormalAttemptState.REQUIRES_FURTHER_REVIEW: "SUBMITTED",
    FormalAttemptState.CERTIFICATE_ALLOWED: "SUBMITTED",
}


class DeviceSessionState(StrEnum):
    """Lifecycle of a device session.

    Sessions are never deleted. "Which device sat this assessment, and was another one turned away?"
    is a question an assessor may need answered months later.
    """

    #: The authoritative session. At most one per formal attempt, enforced by the persistence layer.
    ACTIVE = "ACTIVE"
    #: Closed because the attempt was submitted. The normal ending.
    CLOSED = "CLOSED"
    #: Closed because a disconnect was detected and the attempt auto-submitted.
    DISCONNECTED = "DISCONNECTED"
    #: Recorded but never authoritative: a second device tried to take an already-locked attempt.
    #: Kept as evidence rather than discarded.
    REJECTED = "REJECTED"


class FormalSubmissionReason(StrEnum):
    """Why a formal attempt was committed.

    ``LEARNER_CONFIRMED`` and ``TIME_EXPIRED`` are UC-03's existing ``SubmissionReason`` values,
    reused verbatim. ``DISCONNECT_AUTO_SUBMIT`` is the one UC-09 adds, and the reason it is a
    distinct value rather than a flag is that an assessor reviewing the attempt must be able to see
    that the learner never pressed submit.
    """

    LEARNER_CONFIRMED = "LEARNER_CONFIRMED"
    TIME_EXPIRED = "TIME_EXPIRED"
    DISCONNECT_AUTO_SUBMIT = "DISCONNECT_AUTO_SUBMIT"


class ReviewState(StrEnum):
    """Lifecycle of the human review of a passing formal attempt."""

    #: Persisted the moment the pass is recorded, before any queue is touched.
    PENDING_REVIEW = "PENDING_REVIEW"
    #: An assessor has opened it. Not a lock — it records who is looking, so two assessors do not
    #: unknowingly duplicate the work.
    IN_REVIEW = "IN_REVIEW"
    #: Approved. The certificate gate is now open for this attempt and no other.
    APPROVED = "APPROVED"
    #: Escalated. Terminal for UC-09: the certificate stays blocked, and any further process is the
    #: company's to define. Nothing in this module can turn it into an approval.
    REQUIRES_FURTHER_REVIEW = "REQUIRES_FURTHER_REVIEW"


#: Review states an assessor may still act on.
OPEN_REVIEW_STATES: frozenset[ReviewState] = frozenset(
    {ReviewState.PENDING_REVIEW, ReviewState.IN_REVIEW}
)

#: Review states in which a decision has been made and is final.
DECIDED_REVIEW_STATES: frozenset[ReviewState] = frozenset(
    {ReviewState.APPROVED, ReviewState.REQUIRES_FURTHER_REVIEW}
)


class AssessorDecision(StrEnum):
    """The two decisions an assessor may record. There is no third."""

    APPROVED = "APPROVED"
    REQUIRES_FURTHER_REVIEW = "REQUIRES_FURTHER_REVIEW"


class QueuePublishState(StrEnum):
    """Whether the assessor queue has been told about a pending review.

    This is the outbox: the review record is the durable truth and the queue is a notification. A
    review whose publish state is PENDING or FAILED is still fully reviewable through the API — the
    queue outage delays the assessor's *notification*, never the assessment.
    """

    #: Not published yet, or parked after a transient failure. Retriable.
    PENDING = "PENDING"
    #: The queue accepted it.
    PUBLISHED = "PUBLISHED"
    #: Publishing failed repeatedly. Still retriable, still visible, certificate still blocked.
    FAILED = "FAILED"


#: Publish states the recovery sweep may act on.
RECOVERABLE_PUBLISH_STATES: frozenset[QueuePublishState] = frozenset(
    {QueuePublishState.PENDING, QueuePublishState.FAILED}
)


class CertificateGateDecision(StrEnum):
    """The answer UC-09 gives a certificate service that asks "may I generate?"."""

    #: Not a formal assessment: UC-09 has no opinion and the existing UC-05 rules apply unchanged.
    NOT_FORMAL_ASSESSMENT = "NOT_FORMAL_ASSESSMENT"
    #: A formal pass approved by an authorised assessor.
    ALLOWED = "ALLOWED"
    #: Everything else. ``reason`` says which of them.
    BLOCKED = "BLOCKED"


class CertificateBlockReason(StrEnum):
    """Why a certificate is refused.

    Never merged into one message: an operator needs the specific reason, and a client needs a
    stable code to branch on.
    """

    ATTEMPT_NOT_SUBMITTED = "ATTEMPT_NOT_SUBMITTED"
    RESULT_NOT_CALCULATED = "RESULT_NOT_CALCULATED"
    RESULT_NOT_PASSED = "RESULT_NOT_PASSED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    REQUIRES_FURTHER_REVIEW = "REQUIRES_FURTHER_REVIEW"


class CoachingBlockReason(StrEnum):
    """Why AI coaching (Larry) is refused for a learner right now."""

    #: The learner has a formal assessment in progress — anywhere, on any quiz, on any device.
    FORMAL_ASSESSMENT_IN_PROGRESS = "FORMAL_ASSESSMENT_IN_PROGRESS"
    #: Coaching was requested *about* an attempt that is a formal attempt still in progress.
    FORMAL_ATTEMPT_IN_PROGRESS = "FORMAL_ATTEMPT_IN_PROGRESS"


class IdentityMismatchField(StrEnum):
    """Which piece of the identity confirmation did not match."""

    FULL_NAME = "FULL_NAME"
    EMAIL = "EMAIL"


class FormalAnomalyCode(StrEnum):
    """Conditions worth putting in front of an assessor that are not failures.

    A submitted formal attempt is immutable, so a problem discovered about it is *recorded*, never
    corrected by rewriting the attempt. These are what the assessor's review payload surfaces under
    "anomaly flags".
    """

    #: The learner never pressed submit; a disconnect ended the attempt.
    AUTO_SUBMITTED_AFTER_DISCONNECT = "AUTO_SUBMITTED_AFTER_DISCONNECT"
    #: A second device attempted to take the attempt while it was active.
    SECOND_DEVICE_ATTEMPTED = "SECOND_DEVICE_ATTEMPTED"
    #: A pause or resume was requested and refused.
    PAUSE_OR_RESUME_ATTEMPTED = "PAUSE_OR_RESUME_ATTEMPTED"
    #: An AI coaching request was made and refused during the assessment.
    AI_COACHING_ATTEMPTED = "AI_COACHING_ATTEMPTED"
    #: The identity confirmation was rejected at least once before it succeeded.
    IDENTITY_CONFIRMATION_RETRIED = "IDENTITY_CONFIRMATION_RETRIED"
    #: The auto-submitted state was incomplete: unanswered questions remained when the disconnect
    #: ended the attempt.
    AUTOSAVE_STATE_INCOMPLETE = "AUTOSAVE_STATE_INCOMPLETE"
    #: No autosaved state existed at all when the attempt auto-submitted.
    NO_AUTOSAVED_STATE_AT_DISCONNECT = "NO_AUTOSAVED_STATE_AT_DISCONNECT"
    #: UC-03's attempt status and this record's state disagree.
    UPSTREAM_STATE_MISMATCH = "UPSTREAM_STATE_MISMATCH"


class AnomalySeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"


class FormalAuditEvent(StrEnum):
    """The UC-09 audit vocabulary.

    One value per auditable moment named by the specification. They are *events*, not states: the
    state lives on the record, and these are the immutable trail of how it got there. Every one of
    them is emitted through the audit port in ``integration.audit`` — the platform's existing audit
    pipeline — and UC-09 defines no audit store of its own.
    """

    FORMAL_CONDITIONS_ACKNOWLEDGED = "FORMAL_CONDITIONS_ACKNOWLEDGED"
    IDENTITY_CONFIRMED = "IDENTITY_CONFIRMED"
    #: Not in the specification's minimum list, and included anyway: a rejected identity attempt is
    #: exactly the kind of thing an assessor and an auditor need to see.
    IDENTITY_REJECTED = "IDENTITY_REJECTED"
    FORMAL_ATTEMPT_STARTED = "FORMAL_ATTEMPT_STARTED"
    DEVICE_SESSION_REGISTERED = "DEVICE_SESSION_REGISTERED"
    SECOND_DEVICE_REJECTED = "SECOND_DEVICE_REJECTED"
    PAUSE_REJECTED = "PAUSE_REJECTED"
    RESUME_REJECTED = "RESUME_REJECTED"
    AI_COACHING_BLOCKED = "AI_COACHING_BLOCKED"
    DISCONNECT_DETECTED = "DISCONNECT_DETECTED"
    AUTO_SUBMIT_STARTED = "AUTO_SUBMIT_STARTED"
    AUTO_SUBMIT_COMPLETED = "AUTO_SUBMIT_COMPLETED"
    FORMAL_ATTEMPT_SUBMITTED = "FORMAL_ATTEMPT_SUBMITTED"
    RESULT_CALCULATED = "RESULT_CALCULATED"
    PENDING_REVIEW_CREATED = "PENDING_REVIEW_CREATED"
    ASSESSOR_REVIEW_STARTED = "ASSESSOR_REVIEW_STARTED"
    ASSESSOR_APPROVED = "ASSESSOR_APPROVED"
    REQUIRES_FURTHER_REVIEW = "REQUIRES_FURTHER_REVIEW"
    CERTIFICATE_BLOCKED = "CERTIFICATE_BLOCKED"
    CERTIFICATE_WORKFLOW_TRIGGERED = "CERTIFICATE_WORKFLOW_TRIGGERED"
    QUEUE_FAILURE = "QUEUE_FAILURE"
    QUEUE_RETRY = "QUEUE_RETRY"
    #: Also beyond the specification's minimum: the learner notification is an integration point
    #: that can fail without corrupting anything, so its outcome is recorded rather than inferred.
    LEARNER_NOTIFIED = "LEARNER_NOTIFIED"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"


# UC-09 shipped with its own ``parse_enum`` and ``enum_values`` here, because standalone it had no
# shared kernel to import from. The merged application has exactly one — ``app.core.coercion`` —
# and ``tests/test_architecture.py`` enforces that there is only one, so the copies are gone and
# the single caller imports the shared helper directly.
#
# The shared ``parse_enum`` is strictly more capable than UC-09's was: it tries the value as given,
# then upper-case, then lower-case, where UC-09's only tried upper-case. Nothing here depended on
# the narrower behaviour — every vocabulary UC-09 parses is upper-case — so this widens what is
# accepted at a boundary without changing any value that was already accepted.
