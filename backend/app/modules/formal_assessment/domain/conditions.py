"""The formal assessment conditions (§1).

Seven conditions, defined once, in the backend. The frontend that will eventually render the
conditions screen reads them from :func:`formal_conditions` rather than hard-coding its own copy —
otherwise the text a learner agreed to and the text the system believes they agreed to are two
strings maintained by two teams.

WHY THE CONDITIONS ARE VERSIONED
--------------------------------
An acknowledgement is only meaningful with respect to a specific wording. ``FORMAL_CONDITIONS`` has
a version (from settings, so a deployment can re-issue the text), and the acknowledgement record
stores the version it was made against. A formal attempt started after the text changed requires a
fresh acknowledgement, and "which conditions did this learner accept?" stays answerable years later
from the record alone.

WHY ACKNOWLEDGEMENT IS PER-CONDITION RATHER THAN ONE BOOLEAN
-----------------------------------------------------------
The specification asks the backend to validate ``conditions_acknowledged == true``. A single boolean
from a client is a claim about seven separate facts, and the client is exactly the party that should
not be trusted to summarise them. So the request carries the codes acknowledged, the domain checks
the set is complete, and ``conditions_acknowledged`` becomes something the backend *derives* rather
than something it is told. A client that wants to send one boolean sends the seven codes; the work
is the same and the record is auditable.

None of this is a UI. There is no screen here, no copy deck and no styling — only the canonical
text, its codes, and the completeness rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class FormalConditionCode(StrEnum):
    """The seven conditions a learner must acknowledge, one code each."""

    #: Identity is confirmed against the learner's profile before the assessment starts.
    IDENTITY_CONFIRMATION = "IDENTITY_CONFIRMATION"
    #: The assessment runs on one device only; a second device is refused.
    SINGLE_DEVICE = "SINGLE_DEVICE"
    #: The assessment cannot be paused, and cannot be resumed once it ends.
    NO_PAUSE_OR_RESUME = "NO_PAUSE_OR_RESUME"
    #: A disconnect auto-submits the answers saved so far.
    AUTO_SUBMIT_ON_DISCONNECT = "AUTO_SUBMIT_ON_DISCONNECT"
    #: AI coaching (Larry) is unavailable for the duration.
    NO_AI_COACHING = "NO_AI_COACHING"
    #: A pass is reviewed by a human assessor before anything is issued.
    HUMAN_REVIEW = "HUMAN_REVIEW"
    #: A certificate follows assessor approval, not the pass itself.
    CERTIFICATE_APPROVAL = "CERTIFICATE_APPROVAL"


@dataclass(frozen=True, slots=True)
class FormalCondition:
    """One condition: a stable code, a title, and the sentence a learner is agreeing to."""

    code: FormalConditionCode
    title: str
    statement: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "title": self.title, "statement": self.statement}


#: The canonical conditions, in the order they are presented. Order is part of the contract: a
#: learner reads them in this sequence, and an assessor reviewing an acknowledgement sees the same
#: sequence.
FORMAL_CONDITIONS: tuple[FormalCondition, ...] = (
    FormalCondition(
        code=FormalConditionCode.IDENTITY_CONFIRMATION,
        title="Identity confirmation",
        statement=(
            "You must confirm your identity before starting. The name you enter must match the "
            "name on your profile exactly, and your account email address must already be "
            "confirmed."
        ),
    ),
    FormalCondition(
        code=FormalConditionCode.SINGLE_DEVICE,
        title="One device only",
        statement=(
            "The assessment is locked to the device and browser session you start it on. Opening "
            "it on a second device or a second browser will be refused, and the attempt will be "
            "recorded as having been accessed from more than one device."
        ),
    ),
    FormalCondition(
        code=FormalConditionCode.NO_PAUSE_OR_RESUME,
        title="No pausing, no resuming",
        statement=(
            "The assessment cannot be paused once it starts, and it cannot be resumed after it "
            "ends. The timer runs continuously until you submit or the time expires."
        ),
    ),
    FormalCondition(
        code=FormalConditionCode.AUTO_SUBMIT_ON_DISCONNECT,
        title="Automatic submission if you disconnect",
        statement=(
            "If your session disconnects, the answers saved up to that point are submitted "
            "automatically and the assessment ends. You will not be able to return to it."
        ),
    ),
    FormalCondition(
        code=FormalConditionCode.NO_AI_COACHING,
        title="No AI coaching during the assessment",
        statement=(
            "AI coaching, including Larry, is unavailable from the moment the assessment starts "
            "until it is submitted. Requests made during the assessment are refused and recorded."
        ),
    ),
    FormalCondition(
        code=FormalConditionCode.HUMAN_REVIEW,
        title="Human review of your result",
        statement=(
            "A passing result is reviewed by a qualified assessor before it is finalised. Your "
            "result is held as pending review until that has happened."
        ),
    ),
    FormalCondition(
        code=FormalConditionCode.CERTIFICATE_APPROVAL,
        title="Certificates follow assessor approval",
        statement=(
            "Passing does not by itself produce a certificate. A certificate is issued only after "
            "an authorised assessor has approved your formal assessment."
        ),
    ),
)

#: Every code that must be acknowledged. Derived from the conditions rather than repeated, so adding
#: a condition automatically tightens the completeness rule.
REQUIRED_CONDITION_CODES: frozenset[FormalConditionCode] = frozenset(
    condition.code for condition in FORMAL_CONDITIONS
)


def formal_conditions(version: str) -> dict[str, Any]:
    """The conditions payload a client renders, with the version it must acknowledge."""
    return {
        "conditions_version": version,
        "conditions": [condition.as_dict() for condition in FORMAL_CONDITIONS],
        "required_condition_codes": sorted(code.value for code in REQUIRED_CONDITION_CODES),
    }


def normalise_condition_codes(raw: object) -> tuple[FormalConditionCode, ...]:
    """Parse acknowledged codes, discarding anything unrecognised.

    Unknown codes are dropped rather than rejected: they cannot satisfy a requirement, so the
    completeness check below refuses the acknowledgement anyway, and reporting "unknown code" first
    would tell a client to fix the wrong thing.
    """
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return ()
    parsed: list[FormalConditionCode] = []
    for item in raw:
        text = str(item).strip().upper()
        try:
            code = FormalConditionCode(text)
        except ValueError:
            continue
        if code not in parsed:
            parsed.append(code)
    return tuple(parsed)


def missing_conditions(acknowledged: tuple[FormalConditionCode, ...]) -> tuple[str, ...]:
    """Which required conditions were not acknowledged, in presentation order."""
    present = set(acknowledged)
    return tuple(
        condition.code.value for condition in FORMAL_CONDITIONS if condition.code not in present
    )


def is_acknowledgement_complete(acknowledged: tuple[FormalConditionCode, ...]) -> bool:
    """The backend's own answer to ``conditions_acknowledged == true`` (§1)."""
    return not missing_conditions(acknowledged)
