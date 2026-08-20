"""Request bodies.

Three rules hold across every model here.

**No caller supplies a state, a decision outcome, a result or a permission.** There is no field on
any request that sets a formal state, marks conditions as acknowledged, declares an identity
confirmed, grants a device the session lock, or says a certificate is allowed. Every one of those is
produced by the domain from authoritative data — which is what §19 means when it says none of these
rules may depend on frontend behaviour.

**Validation here is a convenience, not the enforcement.** Every rule in this file is applied again
in the service, because the services are callable directly by a host application that never passed
through the HTTP layer.

**Whitespace is stripped, and nothing else is normalised.** ``str_strip_whitespace=True`` is the
convention UC-03 and UC-08 already use. The identity comparison relies on it and adds only internal-
whitespace collapsing; see ``domain.identity`` for why it stops there. """

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Bounded so an oversized identifier cannot become an oversized key in the company database.
MAX_ID_LENGTH = 64
MAX_NAME_LENGTH = 200
MAX_EMAIL_LENGTH = 320
MAX_FINGERPRINT_LENGTH = 256
MAX_USER_AGENT_LENGTH = 512
MAX_NOTES_LENGTH = 4000
MAX_REASON_LENGTH = 200
#: A registration replay token must be unguessable; see ``domain.idempotency``.
MIN_CLIENT_REQUEST_ID_LENGTH = 16
MAX_CLIENT_REQUEST_ID_LENGTH = 128


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AcknowledgeConditionsRequest(_Request):
    """Acknowledge the formal assessment conditions (§1).

    The learner sends the codes they acknowledged. There is deliberately **no**
    ``conditions_acknowledged`` boolean: a single flag from a client is a claim about seven separate
    facts, and the backend derives it from the codes instead.

    ``conditions_version`` is optional and is checked when supplied — a client that read one version
    of the conditions and posted after they changed is told to re-read them, rather than silently
    acknowledging wording it never showed the learner.
    """

    acknowledged_condition_codes: list[str] = Field(
        min_length=1,
        max_length=32,
        description=(
            "Every condition code the learner acknowledged. All required codes must be present; "
            "the backend derives `conditions_acknowledged` from this list."
        ),
    )
    conditions_version: str | None = Field(
        default=None,
        max_length=32,
        description="The conditions version the learner was shown, if the client tracks it.",
    )

    @field_validator("acknowledged_condition_codes")
    @classmethod
    def _non_blank_codes(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("At least one condition code must be supplied.")
        return cleaned


class ConfirmIdentityRequest(_Request):
    """Confirm identity before a formal assessment (§2).

    ``full_name`` must match the profile name exactly. ``email`` is optional: the *account's* email
    confirmation is required either way, and a deployment may additionally ask the learner to type
    the address, in which case it must match too.
    """

    full_name: str = Field(
        min_length=1,
        max_length=MAX_NAME_LENGTH,
        description="The learner's full name, exactly as it appears on their profile.",
    )
    email: str | None = Field(
        default=None,
        max_length=MAX_EMAIL_LENGTH,
        description="The learner's email address, when the deployment asks them to confirm it.",
    )

    @field_validator("full_name")
    @classmethod
    def _non_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("full_name must not be blank.")
        return value


class DeviceDescriptorRequest(_Request):
    """What the client says about the device.

    **Descriptive only.** None of these fields decides the session lock — the lock is a server-
    issued token and a uniqueness constraint. A fingerprint is recorded as evidence for an assessor,
    because anything the client computes on one device it can compute on another. """

    fingerprint: str | None = Field(
        default=None,
        max_length=MAX_FINGERPRINT_LENGTH,
        description=(
            "The client's own device identifier. Recorded as evidence, never trusted as a lock."
        ),
    )
    platform: str | None = Field(default=None, max_length=MAX_FINGERPRINT_LENGTH)


class StartFormalAttemptRequest(_Request):
    """Start the formal attempt (§3).

    ``client_request_id`` is the one client-supplied idempotency token in UC-09. Supplying it lets a
    retry after a timeout replay the session it already created instead of being refused as a second
    device. Without it, a second registration for a locked attempt is refused — the safe default
    protects the lock.
    """

    device: DeviceDescriptorRequest | None = None
    client_request_id: str | None = Field(
        default=None,
        min_length=MIN_CLIENT_REQUEST_ID_LENGTH,
        max_length=MAX_CLIENT_REQUEST_ID_LENGTH,
        description=(
            "An unguessable token the client generates once per start request, so a retry after a "
            "timeout replays the same session rather than being refused as a second device."
        ),
    )
    retake_of_attempt_id: str | None = Field(
        default=None,
        max_length=MAX_ID_LENGTH,
        description="For a formal retake: the attempt this one follows. Recorded for lineage only.",
    )


class FormalAnswerRequest(_Request):
    """One answer being autosaved.

    ``response`` is opaque: UC-03 owns the answer shapes for all five question types and validates
    them. UC-09 checks who is saving and whether they may.
    """

    question_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    response: object | None = Field(
        default=None,
        description=(
            "The answer payload, in UC-03's format for the question type. Passed through unread."
        ),
    )


class FormalAutosaveRequest(_Request):
    """Autosave answers during a formal attempt (§6).

    All-or-nothing, as UC-03's autosave already is: one rejected answer leaves stored state
    untouched.
    """

    answers: list[FormalAnswerRequest] = Field(min_length=1, max_length=500)


class DisconnectNotificationRequest(_Request):
    """Report that a formal session disconnected (§5).

    ``last_seen_at`` and ``reason`` are descriptive. Nothing in the body decides what gets
    submitted: the answers come from UC-03's latest valid autosaved state, and the instant comes
    from the server clock.
    """

    last_seen_at: str | None = Field(
        default=None,
        max_length=64,
        description="ISO-8601 instant the session was last known to be alive, if known.",
    )
    reason: str | None = Field(
        default=None,
        max_length=MAX_REASON_LENGTH,
        description=(
            "Why the disconnect was declared: heartbeat timeout, browser unload, operator action."
        ),
    )


class AssessorDecisionRequest(_Request):
    """Record an assessor's decision (§10).

    Two decisions, and no field that could imply a third. Notes are for the assessor's reasoning;
    they are stored on the review and shown to whoever reads it afterwards.
    """

    decision: str = Field(
        description="APPROVED or REQUIRES_FURTHER_REVIEW.",
        max_length=32,
    )
    notes: str | None = Field(
        default=None,
        max_length=MAX_NOTES_LENGTH,
        description="The assessor's reasoning. Recorded with the decision.",
    )

    @field_validator("decision")
    @classmethod
    def _non_blank_decision(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("decision must not be blank.")
        return value
