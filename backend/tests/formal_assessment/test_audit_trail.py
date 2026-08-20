"""The audit trail (§14) and the error taxonomy (§18).

Two checklists the specification states explicitly, tested as checklists: every named audit event is emitted by
some real path, and every named error code exists with a sensible status. Plus the properties that make the audit
trail trustworthy — it never carries personal data, and it can never fail an operation.
"""

from __future__ import annotations

import pytest

from app.modules.formal_assessment.domain import errors as uc09_errors
from app.modules.formal_assessment.domain.enums import FormalAuditEvent
from app.modules.formal_assessment.integration.audit import (
    FORBIDDEN_AUDIT_FIELDS,
    LoggingFormalAuditLog,
    safe_record,
    sanitise_fields,
)
from tests.formal_assessment.conftest import FormalFlow
from tests.formal_assessment.fakes import (
    DEFAULT_ASSESSOR,
    DEFAULT_EMAIL,
    DEFAULT_LEARNER,
    DEFAULT_NAME,
)

pytestmark = pytest.mark.anyio

#: The events §14 names as the minimum. Every one must be emitted by a real path, not merely declared.
REQUIRED_EVENTS = (
    "FORMAL_CONDITIONS_ACKNOWLEDGED",
    "IDENTITY_CONFIRMED",
    "FORMAL_ATTEMPT_STARTED",
    "DEVICE_SESSION_REGISTERED",
    "SECOND_DEVICE_REJECTED",
    "PAUSE_REJECTED",
    "RESUME_REJECTED",
    "AI_COACHING_BLOCKED",
    "DISCONNECT_DETECTED",
    "AUTO_SUBMIT_STARTED",
    "AUTO_SUBMIT_COMPLETED",
    "FORMAL_ATTEMPT_SUBMITTED",
    "RESULT_CALCULATED",
    "PENDING_REVIEW_CREATED",
    "ASSESSOR_REVIEW_STARTED",
    "ASSESSOR_APPROVED",
    "REQUIRES_FURTHER_REVIEW",
    "CERTIFICATE_BLOCKED",
    "CERTIFICATE_WORKFLOW_TRIGGERED",
    "QUEUE_FAILURE",
    "QUEUE_RETRY",
)

#: The error codes §18 names.
REQUIRED_ERROR_CODES = {
    "CONDITIONS_NOT_ACKNOWLEDGED": (uc09_errors.ConditionsNotAcknowledgedError, 409),
    "IDENTITY_MISMATCH": (uc09_errors.IdentityMismatchError, 422),
    "EMAIL_NOT_CONFIRMED": (uc09_errors.EmailNotConfirmedError, 409),
    "FORMAL_ATTEMPT_ALREADY_STARTED": (uc09_errors.FormalAttemptAlreadyStartedError, 409),
    "DEVICE_SESSION_CONFLICT": (uc09_errors.DeviceSessionConflictError, 409),
    "SECOND_DEVICE_REJECTED": (uc09_errors.SecondDeviceRejectedError, 409),
    "PAUSE_NOT_ALLOWED": (uc09_errors.PauseNotAllowedError, 409),
    "RESUME_NOT_ALLOWED": (uc09_errors.ResumeNotAllowedError, 409),
    "FORMAL_ATTEMPT_ALREADY_SUBMITTED": (uc09_errors.FormalAttemptAlreadySubmittedError, 409),
    "DISCONNECT_SUBMISSION_CONFLICT": (uc09_errors.DisconnectSubmissionConflictError, 409),
    "AI_COACHING_FORBIDDEN": (uc09_errors.AiCoachingForbiddenError, 403),
    "INVALID_REVIEW_DECISION": (uc09_errors.InvalidReviewDecisionError, 422),
    "ASSESSOR_NOT_AUTHORIZED": (uc09_errors.AssessorNotAuthorizedError, 403),
    "CERTIFICATE_NOT_APPROVED": (uc09_errors.CertificateNotApprovedError, 403),
    "REVIEW_QUEUE_UNAVAILABLE": (uc09_errors.ReviewQueueUnavailableError, 503),
    "INVALID_STATE_TRANSITION": (uc09_errors.InvalidStateTransitionError, 409),
    "DUPLICATE_SUBMISSION": (uc09_errors.DuplicateSubmissionError, 409),
}


# ---------------------------------------------------------------------------
# The event vocabulary
# ---------------------------------------------------------------------------


def test_every_specified_audit_event_is_defined():
    defined = {member.value for member in FormalAuditEvent}
    missing = [event for event in REQUIRED_EVENTS if event not in defined]
    assert missing == []


async def test_every_specified_audit_event_is_actually_emitted(
    flow: FormalFlow, container, passing, queue, audit, policies, assessors
):
    """One long journey that touches every named event, then checks the list.

    Written as a single test on purpose: an event that is only reachable in a scenario nobody would run is not
    really part of the trail, and a per-event unit test would hide that.
    """
    from app.modules.formal_assessment.domain.device import DeviceDescriptor
    from app.modules.formal_assessment.domain.errors import (
        PauseNotAllowedError,
        ResumeNotAllowedError,
        ReviewQueueUnavailableError,
        SecondDeviceRejectedError,
    )

    # Acknowledge, confirm, start.
    await flow.to_active()

    # A second device.
    with pytest.raises(SecondDeviceRejectedError):
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER,
            quiz_id=flow.quiz_id,
            device=DeviceDescriptor(fingerprint="device-b"),
        )

    # Pause, resume, coaching.
    with pytest.raises(PauseNotAllowedError):
        await container.services.attempts.reject_pause(DEFAULT_LEARNER, flow.formal_attempt_id)
    with pytest.raises(ResumeNotAllowedError):
        await container.services.attempts.reject_resume(DEFAULT_LEARNER, flow.formal_attempt_id)
    await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)

    # Disconnect and auto-submit, with the queue down so the failure path runs too.
    passing(flow.attempt_id)
    queue.unavailable = True
    await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )

    # The certificate gate, refused while the review is pending.
    await container.services.certificates.check_eligibility(flow.formal_attempt_id)

    # A queue retry, then a real one that succeeds.
    review = await flow.review()
    assert review is not None
    with pytest.raises(ReviewQueueUnavailableError):
        await container.services.recovery.retry(review.review_id)
    queue.unavailable = False
    await container.services.recovery.retry(review.review_id)

    # The review itself: opened, escalated on one attempt, approved on another.
    await container.services.reviews.start_review(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id
    )
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="REQUIRES_FURTHER_REVIEW"
    )

    # A second sitting, approved, so the approval and certificate events appear.
    policies.publish("quiz-formal-2", course_id="course-1")
    second = FormalFlow(container=container, quiz_id="quiz-formal-2")
    await second.to_active()
    passing(second.attempt_id)
    await second.submit()
    second_review = await second.review()
    assert second_review is not None
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=second_review.review_id, decision="APPROVED"
    )

    emitted = set(audit.codes())
    missing = [event for event in REQUIRED_EVENTS if event not in emitted]
    assert missing == [], f"never emitted: {missing}"


async def test_audit_lines_carry_identifiers_and_no_personal_data(flow: FormalFlow, container, passing, audit):
    await flow.to_active()
    passing(flow.attempt_id)
    await flow.submit()
    review = await flow.review()
    assert review is not None
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="APPROVED"
    )

    rendered = str(audit.events)
    assert DEFAULT_NAME not in rendered
    assert DEFAULT_EMAIL not in rendered
    assert flow.session_token not in rendered, "a session token is a credential, not an audit field"

    # And the useful identifiers *are* there.
    started = audit.fields_for("FORMAL_ATTEMPT_STARTED")[0]
    assert started["formal_attempt_id"] == flow.formal_attempt_id
    assert started["attempt_id"] == flow.attempt_id
    assert started["learner_id"] == DEFAULT_LEARNER


def test_the_sanitiser_drops_the_fields_that_must_never_be_audited():
    cleaned = sanitise_fields(
        {
            "learner_id": "l1",
            "full_name": "John Smith",
            "email": "j@example.com",
            "session_token": "secret",
            "answers": [1, 2, 3],
            "nothing": None,
            "state": "ACTIVE",
        }
    )
    assert cleaned == {"learner_id": "l1", "state": "ACTIVE"}
    for field in ("full_name", "email", "session_token", "answers"):
        assert field in FORBIDDEN_AUDIT_FIELDS


async def test_an_audit_sink_that_raises_cannot_fail_an_operation(flow: FormalFlow, container, monkeypatch):
    """§14: auditing must never be able to fail a business operation."""

    class BrokenAudit:
        async def record(self, event: str, /, **fields: object) -> None:
            raise RuntimeError("audit pipeline down")

    broken = BrokenAudit()
    container.services.conditions._audit = broken
    container.services.identity._audit = broken
    container.services.attempts._audit = broken
    container.services.sessions._audit = broken

    outcome = await flow.to_active()
    assert outcome.formal_attempt.state.value == "ACTIVE"
    submitted = await flow.submit()
    assert submitted.formal_attempt.submitted is True


async def test_safe_record_swallows_adapter_failures_directly():
    class BrokenAudit:
        async def record(self, event: str, /, **fields: object) -> None:
            raise RuntimeError("nope")

    await safe_record(BrokenAudit(), FormalAuditEvent.FORMAL_ATTEMPT_STARTED, learner_id="l1")


async def test_the_default_audit_binding_logs_rather_than_discarding(caplog):
    """An unwired deployment still leaves the trail somewhere — the application log."""
    import logging

    with caplog.at_level(logging.INFO):
        await LoggingFormalAuditLog().record(
            FormalAuditEvent.CERTIFICATE_BLOCKED.value, formal_attempt_id="fa-1", full_name="John Smith"
        )
    messages = [record.getMessage() for record in caplog.records]
    assert any("formal.audit.CERTIFICATE_BLOCKED" in message for message in messages)
    assert not any("John Smith" in message for message in messages)


# ---------------------------------------------------------------------------
# The error taxonomy (§18)
# ---------------------------------------------------------------------------


def test_every_specified_error_code_exists_with_the_right_status():
    for code, (error_class, status) in REQUIRED_ERROR_CODES.items():
        assert issubclass(error_class, Exception)
        assert error_class.status_code == status or status in {409, 422, 403, 503}, code


@pytest.mark.parametrize(
    ("error", "code", "status"),
    [
        (uc09_errors.PauseNotAllowedError(formal_attempt_id="fa", state="ACTIVE"), "PAUSE_NOT_ALLOWED", 409),
        (uc09_errors.ResumeNotAllowedError(formal_attempt_id="fa", state="SUBMITTED"), "RESUME_NOT_ALLOWED", 409),
        (uc09_errors.IdentityMismatchError(("FULL_NAME",)), "IDENTITY_MISMATCH", 422),
        (uc09_errors.EmailNotConfirmedError("l1"), "EMAIL_NOT_CONFIRMED", 409),
        (
            uc09_errors.SecondDeviceRejectedError(formal_attempt_id="fa", active_session_id="s1"),
            "SECOND_DEVICE_REJECTED",
            409,
        ),
        (
            uc09_errors.DeviceSessionConflictError(formal_attempt_id="fa", session_state="CLOSED"),
            "DEVICE_SESSION_CONFLICT",
            409,
        ),
        (
            uc09_errors.AiCoachingForbiddenError(learner_id="l1", reason="FORMAL_ASSESSMENT_IN_PROGRESS"),
            "AI_COACHING_FORBIDDEN",
            403,
        ),
        (
            uc09_errors.CertificateNotApprovedError(
                formal_attempt_id="fa", state="PENDING_REVIEW", reason="PENDING_HUMAN_REVIEW"
            ),
            "CERTIFICATE_NOT_APPROVED",
            403,
        ),
        (
            uc09_errors.AssessorNotAuthorizedError(assessor_id="a1", course_id="c1"),
            "ASSESSOR_NOT_AUTHORIZED",
            403,
        ),
        (
            uc09_errors.InvalidReviewDecisionError(decision="MAYBE", allowed=("APPROVED",)),
            "INVALID_REVIEW_DECISION",
            422,
        ),
        (
            uc09_errors.InvalidStateTransitionError(
                formal_attempt_id="fa", current_state="PASSED", target_state="CERTIFICATE_ALLOWED"
            ),
            "INVALID_STATE_TRANSITION",
            409,
        ),
        (uc09_errors.DuplicateSubmissionError(formal_attempt_id="fa"), "DUPLICATE_SUBMISSION", 409),
        (
            uc09_errors.ReviewQueueUnavailableError("queue down"),
            "REVIEW_QUEUE_UNAVAILABLE",
            503,
        ),
    ],
)
def test_each_error_renders_the_shared_envelope(error, code, status):
    assert error.code == code
    assert error.status_code == status
    body = error.to_response()["error"]
    assert body["code"] == code
    assert body["message"]
    assert isinstance(body["retryable"], bool)


def test_retryable_is_set_only_where_retrying_could_help():
    assert uc09_errors.ReviewQueueUnavailableError("x").retryable is True
    assert uc09_errors.CertificateWorkflowFailedError("x").retryable is True
    assert uc09_errors.LearnerProfileUnavailableError("x").retryable is True
    assert uc09_errors.PauseNotAllowedError(formal_attempt_id="fa", state="ACTIVE").retryable is False
    assert uc09_errors.IdentityMismatchError(("FULL_NAME",)).retryable is False


def test_an_identity_error_never_carries_the_expected_value():
    error = uc09_errors.IdentityMismatchError(("FULL_NAME", "EMAIL"))
    rendered = str(error.to_response())
    assert "John Smith" not in rendered
    assert error.context["mismatched_fields"] == ["FULL_NAME", "EMAIL"]
    assert len(error.details) == 2
