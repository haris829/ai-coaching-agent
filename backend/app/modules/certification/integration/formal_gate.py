"""UC-09 (Formal Assessment Mode) — the contract UC-05 consumes before generating a certificate.

One question, asked at the single point where a certificate is generated: *may I?*

WHY THE GATE LIVES IN UC-05 AND THE ANSWER LIVES IN UC-09
---------------------------------------------------------
UC-05 owns certificates: the lifecycle, the duplicate prevention, the CPD hand-off, the retry. It
must keep owning them, or a formal assessment would end up with a second certificate mechanism.
What UC-09 owns is one condition — *a human has approved this pass* — and a condition is a fact, not
a workflow. So UC-05 asks, and UC-09 answers.

The two directions are not a cycle. This port is "may I generate?"; UC-09's ``CertificateWorkflow``
port is "you may now, an assessor approved it". One is a question about state, the other an
instruction that follows a human decision.

THE UNBOUND DEFAULT ALLOWS
--------------------------
:class:`UnrestrictedCertificateGate` allows everything, which is the truth for a deployment with no
UC-09: without formal assessments there is nothing to withhold, and defaulting to "blocked" would
stop every ordinary learner receiving the certificate they earned.

That default is safe only because the two failure modes are kept apart. An *absent* UC-09 allows. A
*bound but unreadable* UC-09 raises, and the certificate stays PENDING — "we could not confirm an
assessor approved this" must never become "issue it".

NOTHING HERE CHANGES A STANDARD QUIZ
------------------------------------
``NOT_FORMAL_ASSESSMENT`` is the answer for every attempt that is not a supervised sitting, and
UC-05's existing rules then apply unchanged. That is the overwhelming majority of attempts, and it
is why this gate could be added to a working certificate flow without touching what it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class CertificateGateDecision(StrEnum):
    """Why a certificate may or may not be generated for one attempt."""

    #: Not a formal assessment. UC-05's own rules decide, exactly as before UC-09 existed.
    NOT_FORMAL_ASSESSMENT = "NOT_FORMAL_ASSESSMENT"
    #: A formal assessment whose conditions are satisfied — approved, or approval not required.
    ALLOWED = "ALLOWED"
    #: A formal assessment still waiting on something. ``reason`` says what.
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class CertificateGateResult:
    decision: CertificateGateDecision
    #: A stable code naming what is being waited for — ``AWAITING_ASSESSOR_APPROVAL``,
    #: ``REQUIRES_FURTHER_REVIEW``, ``RESULT_NOT_RESOLVED``. Rendered to an operator, never parsed
    #: to decide anything.
    reason: str | None = None
    review_id: str | None = None

    @property
    def certificate_allowed(self) -> bool:
        return self.decision is not CertificateGateDecision.BLOCKED


@runtime_checkable
class CertificateGatePort(Protocol):
    """Read-only port onto UC-09's certificate gate.

    There is deliberately no method here that could *approve* anything. UC-05 asks whether it may
    generate; it cannot make itself eligible.
    """

    def check_attempt(self, attempt_id: str) -> CertificateGateResult:
        """Whether a certificate may be generated for this attempt.

        Must raise rather than return ``ALLOWED`` when UC-09 cannot be read.
        """
        ...


class UnrestrictedCertificateGate:
    """The unbound default: nothing is a formal assessment, so nothing is withheld."""

    __slots__ = ()

    def check_attempt(self, attempt_id: str) -> CertificateGateResult:
        return CertificateGateResult(decision=CertificateGateDecision.NOT_FORMAL_ASSESSMENT)
