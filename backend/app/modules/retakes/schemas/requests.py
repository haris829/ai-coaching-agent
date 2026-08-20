"""Request bodies.

Two rules hold across every model here.

**No caller supplies a count, a state or an entitlement.** There is no field on any request that
sets an attempt count, a remaining-attempts figure, an eligibility state or a retake status. Those
are produced by the domain from authoritative data, which is what §1 means by "do not trust
frontend-provided attempt counts". ``additional_attempts`` is the single number a caller may send,
it is an administrator's decision rather than an observation, and it is bounded on both sides.

**Validation here is a convenience, not the enforcement.** Every rule in this file is applied again
in the service, because the services are callable directly by a host application that never passed
through the HTTP layer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Bounded so an oversized identifier cannot become an oversized key in the company database.
MAX_ID_LENGTH = 64
MAX_REASON_LENGTH = 500


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateRetakeRequest(_Request):
    """Start a retake.

    ``previous_attempt_id`` is optional: omitted, the learner's most recent submitted attempt is
    retaken, which is the only attempt that *can* be retaken anyway. Naming it explicitly is what a
    client does when it wants to be told "that attempt has been superseded" rather than silently
    retaking a newer one it did not know about.

    There is deliberately no idempotency-key field. The key is derived from the learner, the quiz
    and the previous attempt, so a retried request converges without the client having had to
    remember anything (§16).
    """

    previous_attempt_id: str | None = Field(
        default=None,
        max_length=MAX_ID_LENGTH,
        description=(
            "The completed attempt to retake. Defaults to the learner's most recent "
            "submitted attempt."
        ),
    )

    @field_validator("previous_attempt_id")
    @classmethod
    def _non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("previous_attempt_id must not be blank.")
        return value


class CreateGrantRequest(_Request):
    """Grant a learner additional attempts at one quiz.

    ``granted_by`` is absent by design: the administrator identity comes from the authorisation
    seam, never from the body, so a caller cannot attribute a grant to someone else.
    """

    learner_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    quiz_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    #: Optional. When supplied it is checked against the course UC-01 says the quiz belongs to,
    #: rather than trusted — a grant scoped to the wrong course is exactly what §12 guards against.
    course_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    #: Lower bound 1: a grant that added nothing would be reported as a success (§12).
    additional_attempts: int = Field(
        default=1, ge=1, le=100, description="Extra attempts to grant. Never negative or zero."
    )
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    #: Required, in the body or the ``Idempotency-Key`` header. See ``domain.idempotency``.
    idempotency_key: str | None = Field(default=None, max_length=128)


class RevokeGrantRequest(_Request):
    """Withdraw a grant whose attempts have not been used."""

    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)
