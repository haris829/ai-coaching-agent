"""UC-01 (Quiz Configuration) — the contract UC-09 consumes.

UC-09 needs three facts from UC-01 and nothing else:

* is this quiz a **formal assessment**?
* which course does it belong to (the assessor's authorisation is course-scoped)?
* is it available to be sat at all?

WHERE THE "IS FORMAL" FLAG COMES FROM
-------------------------------------
It is a property of the quiz configuration, not of the request. That is the whole point: a client
cannot ask for an ordinary attempt at a formal quiz, and cannot ask for a formal attempt at an
ordinary one. At integration this maps onto one boolean (or one delivery-mode value) on UC-01's
existing immutable configuration version — ``docs/INTEGRATION.md`` gives the mapping. It is read
from the
*version locked to the attempt* wherever a locked version is available, so a quiz made formal
tomorrow does not retroactively change what a learner sat today.

``requires_human_review`` and ``requires_assessor_approval`` are carried separately from
``is_formal_assessment`` because a company may configure a formal assessment that does not gate
certificates, and the certificate gate must key on the configured fact rather than inferring it.
Both default to True for a formal assessment: the safe default is the supervised one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FormalAssessmentPolicy:
    """What UC-01 says about how this quiz must be sat."""

    quiz_id: str
    course_id: str
    #: The flag everything in UC-09 hangs off.
    is_formal_assessment: bool
    #: Whether the quiz may be sat right now. UC-03 also checks this; UC-09 checks it before
    #: creating
    #: a formal record so a withdrawn quiz produces one refusal rather than a half-built gate.
    available: bool = True
    unavailable_reason: str | None = None
    #: A passing formal attempt waits for a human. False only if a deployment explicitly configures
    #: a
    #: formal assessment without review.
    requires_human_review: bool = True
    #: A certificate needs assessor approval. The certificate gate reads this.
    requires_assessor_approval: bool = True
    course_name: str | None = None
    quiz_title: str | None = None
    #: The immutable configuration version this policy was read from, when known. Recorded on the
    #: formal attempt so an assessor can see which rules were in force.
    configuration_version_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "quiz_id": self.quiz_id,
            "course_id": self.course_id,
            "is_formal_assessment": self.is_formal_assessment,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "requires_human_review": self.requires_human_review,
            "requires_assessor_approval": self.requires_assessor_approval,
            "course_name": self.course_name,
            "quiz_title": self.quiz_title,
            "configuration_version_id": self.configuration_version_id,
        }


@runtime_checkable
class FormalAssessmentPolicyProvider(Protocol):
    """Read-only. UC-09 never writes a quiz configuration."""

    async def get_policy(self, quiz_id: str) -> FormalAssessmentPolicy | None:
        """The formal-assessment policy for a quiz, or ``None`` if the quiz does not exist.

        Should raise ``ProviderUnavailableError`` when UC-01 cannot be reached, rather than
        returning ``None``: "the quiz does not exist" and "we could not check" lead to different
        refusals, and conflating them would let an unreachable configuration service look like a
        missing quiz.
        """
        ...

    async def get_policy_for_attempt(self, attempt_id: str) -> FormalAssessmentPolicy | None:
        """The policy that applied to an existing attempt, read from its locked version.

        Used by the certificate gate and the coaching check, which are asked about an attempt rather
        than about a quiz. An implementation that cannot resolve an attempt's locked version may
        fall back to the quiz's current policy — but should prefer the locked one, so an assessment
        already sat is judged by the rules it was sat under.
        """
        ...
