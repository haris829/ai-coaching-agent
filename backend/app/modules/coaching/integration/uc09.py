"""UC-09 (Formal Assessment Mode) — the contract UC-07 consumes.

One question, asked on **every** coaching operation: *is a formal assessment of this learner's in
progress right now?*

WHY IT IS LEARNER-SCOPED
------------------------
The obvious design is to ask "is *this attempt* a formal assessment still running?" — and it is
wrong. A learner coaching an older, submitted, fully scored attempt while a supervised exam sits
open in another tab is exactly the case the restriction exists for, and an attempt-scoped check
waves it through. So the port takes the learner, and the attempt only for the audit record.

WHY IT IS ASKED EVERY TIME
--------------------------
A session legitimately opened at 10:00 must stop working when a formal assessment starts at 10:05.
UC-07's authoriser already re-reads every fact on every operation rather than trusting an earlier
decision, and this joins that set. Checking only at session start would leave a live channel into
the model attached to a state that has since changed.

THE UNBOUND DEFAULT ALLOWS
--------------------------
:class:`UnrestrictedFormalAssessmentPolicy` reports "no formal assessment in progress", which is
the truth for a deployment with no UC-09 — and it is the one honest default here, unlike UC-07's
other ports where the safe answer is to refuse. Refusing by default would disable coaching entirely
for every learner in a system that has no formal assessments at all.

The distinction that makes that safe: an *unavailable* UC-09 is not the same as an absent one. A
bound adapter that cannot read raises, and the refusal propagates — "we could not confirm this
learner is not sitting an exam" must never degrade into "coaching allowed".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FormalAssessmentStatus:
    """Whether a supervised sitting is currently blocking coaching for this learner."""

    #: True while any formal assessment of theirs is open.
    in_progress: bool
    #: The blocking sitting, for the audit record and for a message that names it. Never used to
    #: decide anything — ``in_progress`` is the decision.
    formal_attempt_id: str | None = None
    quiz_id: str | None = None


@runtime_checkable
class FormalAssessmentPolicyPort(Protocol):
    """Read-only port onto UC-09.

    Read-only in the strongest sense: there is no method here that could start, submit, approve or
    otherwise touch a formal assessment. UC-07 asks a question and acts on the answer.
    """

    async def get_status_for_learner(self, learner_id: str) -> FormalAssessmentStatus:
        """Whether any formal assessment of this learner's is in progress.

        Must raise rather than return ``in_progress=False`` when UC-09 cannot be read: a failure
        to confirm is not a confirmation.
        """
        ...


class UnrestrictedFormalAssessmentPolicy:
    """The unbound default: no formal assessment is in progress, because there is no UC-09."""

    __slots__ = ()

    async def get_status_for_learner(self, learner_id: str) -> FormalAssessmentStatus:
        return FormalAssessmentStatus(in_progress=False)
