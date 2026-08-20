"""API response shapes.

Read models only. Each is built from the corresponding domain object's ``as_dict()`` via
``model_validate``, so the HTTP contract is generated from the domain rather than hand-copied
alongside it and cannot quietly drift from it.

Two conventions worth stating:

**Every gate reports its reason.** A blocked certificate, a refused coaching request and a refused
start all come back with a machine-readable reason code as well as a message, so a client can branch
without parsing prose — and so a client never has to reconstruct the decision itself.

**The session token appears in exactly one response.** ``FormalAttemptStartResponse`` returns it
once, to the device that registered. No read endpoint includes it, because a token that can be
fetched again is not a credential for the device that holds it. """

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ConditionModel(_Model):
    code: str
    title: str
    statement: str


class FormalConditionsResponse(_Model):
    """The conditions a learner must acknowledge, and the policy that applies (§1)."""

    quiz_id: str
    course_id: str
    is_formal_assessment: bool
    requires_human_review: bool
    requires_assessor_approval: bool
    conditions_version: str
    conditions: list[ConditionModel]
    required_condition_codes: list[str]


class AnomalyModel(_Model):
    code: str
    severity: str
    message: str
    occurrences: int = 1
    first_observed_at: str | None = None
    last_observed_at: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ConditionsAcknowledgementModel(_Model):
    conditions_version: str
    acknowledged_codes: list[str]
    acknowledged_at: str
    acknowledged: bool


class IdentityConfirmationModel(_Model):
    confirmed_at: str
    email_confirmed: bool
    email_supplied: bool = False
    rejected_attempts: int = 0


class FormalResultModel(_Model):
    """The result as UC-04 and UC-05 decided it. A copy, never recalculated here (§8)."""

    result_status: str
    passed: bool
    calculated_at: str
    percentage: float | None = None
    pass_mark: float | None = None
    total_marks: float | None = None
    maximum_marks: float | None = None
    score_status: str | None = None
    result_id: str | None = None


class DisconnectModel(_Model):
    detected_at: str
    reported_by: str
    last_seen_at: str | None = None
    autosaved_at: str | None = None
    answered_questions: int | None = None
    total_questions: int | None = None
    reason: str | None = None


class FormalAttemptModel(_Model):
    """One formal attempt. The shape every learner-facing endpoint returns."""

    formal_attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    state: str
    attempt_id: str | None = None
    attempt_number: int | None = None
    configuration_version_id: str | None = None
    retake_of_attempt_id: str | None = None
    conditions_acknowledged: bool = False
    conditions: ConditionsAcknowledgementModel | None = None
    identity_confirmed: bool = False
    identity: IdentityConfirmationModel | None = None
    device_session_id: str | None = None
    started_at: str | None = None
    submitted_at: str | None = None
    submission_reason: str | None = None
    auto_submitted: bool = False
    disconnect: DisconnectModel | None = None
    result: FormalResultModel | None = None
    review_id: str | None = None
    certificate_allowed: bool = False
    certificate_workflow_triggered_at: str | None = None
    certificate_reference: str | None = None
    anomalies: list[AnomalyModel] = Field(default_factory=list)
    created_at: str
    updated_at: str
    version: int = 1


class AcknowledgementResponse(FormalAttemptModel):
    """The formal attempt after acknowledgement. ``created`` is False when a retry found the record.
    """

    created: bool = False
    conditions_version: str


class IdentityCheckModel(_Model):
    confirmed: bool
    email_confirmed: bool
    mismatched_fields: list[str] = Field(default_factory=list)
    name_match_rule: str


class IdentityConfirmationResponse(FormalAttemptModel):
    identity_check: IdentityCheckModel


class DeviceDescriptorModel(_Model):
    fingerprint: str | None = None
    user_agent: str | None = None
    ip_address: str | None = None
    platform: str | None = None


class DeviceSessionModel(_Model):
    """A device session. The token is present only in the start response (see the module docstring).
    """

    session_id: str
    formal_attempt_id: str
    learner_id: str
    state: str
    registered_at: str
    last_seen_at: str | None = None
    closed_at: str | None = None
    closed_reason: str | None = None
    superseded_by_session_id: str | None = None
    device: DeviceDescriptorModel = DeviceDescriptorModel()
    session_token: str | None = Field(
        default=None,
        description=(
            "Returned once, to the device that registered the session. Present it in the "
            "`X-Formal-Session` header on every later operation on this attempt."
        ),
    )


class FormalAttemptStartResponse(FormalAttemptModel):
    """The started formal attempt and its session (§3)."""

    session: DeviceSessionModel
    replayed: bool = False


class AutosavedStateModel(_Model):
    """The latest valid autosaved state — distinct from the submitted state and the result (§6)."""

    attempt_id: str
    exists: bool = True
    saved_at: str | None = None
    answered_questions: int = 0
    total_questions: int | None = None
    complete: bool = False


class SubmittedStateModel(_Model):
    attempt_id: str
    submitted_at: str
    submission_reason: str | None = None
    answered_questions: int | None = None
    total_questions: int | None = None
    already_submitted: bool = False


class FormalAttemptStatusResponse(FormalAttemptModel):
    """The authoritative status of a formal attempt (§17)."""

    upstream_attempt: dict[str, Any] | None = None
    autosaved_state: AutosavedStateModel | None = None
    session_state: str | None = None
    #: Always False for a formal attempt. Stated by the backend so a client renders rather than
    #: decides.
    pause_allowed: bool = False
    resume_allowed: bool = False
    ai_coaching_allowed: bool = True


class FormalSubmissionResponse(FormalAttemptModel):
    """The submitted formal attempt. ``replayed`` marks a duplicate that changed nothing (§20)."""

    submitted_state: SubmittedStateModel | None = None
    replayed: bool = False
    auto_submitted: bool = False


class FormalAutosaveResponse(_Model):
    attempt_id: str
    saved_count: int = 0
    changed_count: int = 0
    persisted_at: str | None = None
    answered_questions: int | None = None
    total_questions: int | None = None


class SessionHeartbeatResponse(_Model):
    session_id: str
    formal_attempt_id: str
    state: str
    last_seen_at: str | None = None
    #: How long the session may go unseen before the platform monitor may declare a disconnect.
    heartbeat_timeout_seconds: int


class AiCoachingEligibilityResponse(_Model):
    """Whether Larry may run for this learner right now (§7)."""

    ai_coaching_allowed: bool
    reason: str | None = None
    message: str | None = None
    formal_attempt_id: str | None = None
    quiz_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CertificateEligibilityResponse(_Model):
    """The certificate gate's verdict (§11)."""

    decision: str
    certificate_allowed: bool
    formal_assessment: bool
    reason: str | None = None
    message: str | None = None
    formal_attempt_id: str | None = None
    state: str | None = None
    review_id: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class CertificateTriggerResponse(_Model):
    """What a certificate trigger did (§11, §12)."""

    formal_attempt_id: str
    state: str
    triggered: bool
    replayed: bool = False
    reference: str | None = None
    workflow_status: str | None = None
    certificate_workflow_triggered_at: str | None = None
    eligibility: CertificateEligibilityResponse


class QueueStateModel(_Model):
    publish_state: str
    publish_attempts: int = 0
    published_at: str | None = None
    last_publish_error: str | None = None
    last_publish_attempt_at: str | None = None


class AssessorDecisionModel(_Model):
    decision: str
    decided_by: str
    decided_at: str
    notes: str | None = None


class FormalReviewModel(_Model):
    """One review, as an assessor's queue lists it."""

    review_id: str
    formal_attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_id: str
    state: str
    percentage: float | None = None
    submitted_at: str | None = None
    auto_submitted: bool = False
    anomaly_count: int = 0
    assigned_to: str | None = None
    review_started_at: str | None = None
    decision: AssessorDecisionModel | None = None
    queue: QueueStateModel
    created_at: str
    updated_at: str
    version: int = 1


class PendingReviewsResponse(_Model):
    """The assessor's queue, oldest first (§9)."""

    reviews: list[FormalReviewModel] = Field(default_factory=list)
    total_pending: int = 0
    limit: int = 50
    offset: int = 0
    #: The courses the list was scoped to. ``null`` only for a platform-wide assessor.
    course_ids: list[str] | None = None


class ReviewDetailResponse(_Model):
    """Everything an assessor needs to decide (§10).

    ``learner`` includes the name and email address — read live from the profile source for the
    authorised assessor who asked, and stored nowhere in UC-09 — because confirming that the right
    person sat the assessment cannot be done from an opaque identifier.
    """

    review: FormalReviewModel
    formal_attempt: FormalAttemptModel
    learner: dict[str, Any]
    assessment: dict[str, Any]
    score: FormalResultModel | None = None
    responses: list[dict[str, Any]] = Field(default_factory=list)
    attempt: dict[str, Any] | None = None
    anomalies: list[AnomalyModel] = Field(default_factory=list)
    submission: dict[str, Any]
    disconnect: DisconnectModel | None = None
    supervision: dict[str, Any]


class DecisionResponse(_Model):
    """The recorded decision and what followed from it (§10, §11, §12)."""

    review: FormalReviewModel
    formal_attempt: FormalAttemptModel
    certificate: CertificateTriggerResponse | None = None
    #: ``null`` when no notification was attempted, True/False when one was.
    notification_delivered: bool | None = None


class UnpublishedReviewsResponse(_Model):
    """Reviews the assessor queue has not accepted (§13). None of them is lost."""

    reviews: list[FormalReviewModel] = Field(default_factory=list)
    count: int = 0


class RecoveryReportResponse(_Model):
    considered: int = 0
    published: int = 0
    still_pending: int = 0
    review_ids: list[str] = Field(default_factory=list)


class ResolutionResponse(FormalAttemptModel):
    """The formal attempt after a result-resolution attempt (§8)."""

    resolution_outcome: str
    resolution_reason: str | None = None
