"""API response shapes.

Read models only. Each is built from the corresponding domain object's ``as_dict()`` via
``model_validate``, so the HTTP contract is generated from the domain rather than hand-copied
alongside it and cannot quietly drift from it.

Every response that reports an eligibility decision carries the arithmetic behind it — the
maximum, the used count, the granted attempts and the entitlement — so a caller never has to
reconstruct the calculation, and never has any reason to try.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AnomalyModel(_Model):
    code: str
    severity: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class BlockerModel(_Model):
    """One reason a retake cannot be created."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AllowanceModel(_Model):
    """The attempt allowance. ``available_attempts`` is ``null`` only when unlimited."""

    maximum_attempts: int | None = None
    attempts_used: int = 0
    granted_attempts: int = 0
    total_entitlement: int | None = None
    available_attempts: int | None = None
    has_available_attempts: bool = True
    unlimited: bool = False
    relies_on_grant: bool = False


class EligibilityResponse(_Model):
    """The authoritative answer to "may this learner retake?" (§2)."""

    learner_id: str
    quiz_id: str
    course_id: str | None = None
    state: str
    can_retake: bool
    allowance: AllowanceModel
    blockers: list[BlockerModel] = Field(default_factory=list)
    previous_attempt_id: str | None = None
    previous_attempt_number: int | None = None
    next_attempt_number: int | None = None
    configuration_version_id: str | None = None
    configuration_version_number: int | None = None
    configuration_version_source: str | None = None
    #: Administrator-contact guidance, present only when the allowance is spent (§13).
    guidance: str | None = None
    anomalies: list[AnomalyModel] = Field(default_factory=list)


class TypeAvailabilityModel(_Model):
    type: str
    required: int
    eligible: int
    unused: int


class QuestionPlanModel(_Model):
    """What the retake was told to avoid, and what the bank could support (§6, §8)."""

    required_count: int
    eligible_pool_size: int
    unused_pool_size: int
    excluded_question_count: int
    exclusion_scope: str
    reuse_expected: bool
    reuse_reason: str | None = None
    expected_fresh_questions: int
    type_availability: list[TypeAvailabilityModel] = Field(default_factory=list)
    feasible: bool = True
    shortfalls: list[dict[str, Any]] = Field(default_factory=list)


class QuestionSetDifferenceModel(_Model):
    """How the retake's paper compares with the one it followed (§7)."""

    previous_question_count: int
    retake_question_count: int
    new_question_count: int
    repeated_question_count: int
    unseen_question_count: int
    expected_fresh_questions: int
    identical_question_set: bool
    satisfied: bool
    reuse_unavoidable: bool
    repeated_question_ids: list[str] = Field(default_factory=list)


class DeliveredAttemptModel(_Model):
    """The attempt UC-03 created. Question *ids* only — UC-08 never handles question content."""

    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_number: int
    status: str
    configuration_version_id: str
    configuration_version_number: int | None = None
    delivered_question_ids: list[str] = Field(default_factory=list)
    total_questions: int = 0
    started_at: str | None = None
    delivery_mode: str | None = None
    time_limit_seconds: int | None = None


class RetakeModel(_Model):
    """The retake record: the reservation, the lineage and what came of it."""

    retake_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    previous_attempt_id: str
    attempt_id: str | None = None
    attempt_number: int
    status: str
    configuration_version_id: str
    configuration_version_number: int | None = None
    configuration_version_source: str
    requested_at: str
    completed_at: str | None = None
    updated_at: str
    question_plan: QuestionPlanModel | None = None
    question_set_difference: QuestionSetDifferenceModel | None = None
    anomalies: list[AnomalyModel] = Field(default_factory=list)
    failure_code: str | None = None
    failure_message: str | None = None
    attempt_count: int = 1


class RetakeResponse(_Model):
    """The full result of a retake request."""

    retake: RetakeModel
    attempt: DeliveredAttemptModel | None = None
    eligibility: EligibilityResponse
    question_plan: QuestionPlanModel | None = None
    question_set_difference: QuestionSetDifferenceModel | None = None
    #: True when the request was a repeat and the attempt already existed (§16).
    replayed: bool = False


class RetakeListResponse(_Model):
    learner_id: str
    quiz_id: str
    retakes: list[RetakeModel] = Field(default_factory=list)


class AttemptHistoryEntryModel(_Model):
    """One attempt as history shows it. Missing upstream data is labelled, never invented."""

    attempt_id: str
    attempt_number: int
    status: str
    configuration_version_id: str
    configuration_version_number: int | None = None
    started_at: str | None = None
    submitted_at: str | None = None
    total_questions: int | None = None
    score_available: bool = False
    total_marks: float | None = None
    maximum_marks: float | None = None
    percentage: float | None = None
    pass_fail_available: bool = False
    pass_fail_status: str | None = None
    pass_mark_percentage: float | None = None
    feedback_available: bool = False
    coaching_available: bool = False
    is_retake: bool = False
    retake_of_attempt_id: str | None = None
    retake_id: str | None = None
    retaken_by_attempt_id: str | None = None


class AttemptHistoryResponse(_Model):
    learner_id: str
    quiz_id: str
    course_id: str | None = None
    attempt_count: int = 0
    entries: list[AttemptHistoryEntryModel] = Field(default_factory=list)


class GrantModel(_Model):
    grant_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    additional_attempts: int
    granted_by: str
    granted_at: str
    status: str
    reason: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None


class GrantResponse(_Model):
    grant: GrantModel
    #: True when the idempotency key had already been used for this same grant (§14).
    replayed: bool = False


class GrantListResponse(_Model):
    learner_id: str
    quiz_id: str
    course_id: str
    #: The course-wide maximum, shown alongside the grants precisely because a grant does not
    #: change it (§11).
    configured_maximum_attempts: int | None = None
    granted_attempts: int = 0
    grants: list[GrantModel] = Field(default_factory=list)
