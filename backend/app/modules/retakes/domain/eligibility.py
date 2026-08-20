"""Retake eligibility (§1, §2, §13).

The business layer answers "may this learner retake this quiz?" and the user-facing layer renders
the answer. A frontend cannot compute this: it cannot see administrator grants, it cannot see
another request's in-flight reservation, and it does not know which configuration version the
learner's history is locked to. Disabling a button is presentation, never enforcement — the same
rules run again inside retake creation, from the same functions.

The four states §2 names, and what puts a learner in each:

===============================  ==========================================================
``ELIGIBLE``                      Configured attempts remain.
``ADDITIONAL_ATTEMPT_AVAILABLE``  Attempts remain *only* because an administrator granted one.
``EXHAUSTED``                     The allowance is spent, and nothing else is wrong.
``UNAVAILABLE``                   Something other than the allowance prevents a retake — an
                                  attempt still open, no completed attempt to retake, a
                                  withdrawn quiz, an upstream module that could not be read.
===============================  ==========================================================

``EXHAUSTED`` is deliberately narrow. A learner who is both out of attempts *and* blocked by a
withdrawn quiz is ``UNAVAILABLE``: telling them to ask an administrator for another attempt would
send them to ask for something that would not help. Every blocker is still listed, so nothing is
hidden by the choice of headline state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.retakes.domain.allowance import AttemptAllowance
from app.modules.retakes.domain.anomalies import RetakeAnomaly
from app.modules.retakes.domain.enums import (
    ConfigurationVersionSource,
    RetakeBlockerCode,
    RetakeState,
)


@dataclass(frozen=True, slots=True)
class RetakeBlocker:
    """One reason a retake cannot be created. Several may apply at once."""

    code: RetakeBlockerCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}


def blocker(code: RetakeBlockerCode, message: str, **details: Any) -> RetakeBlocker:
    return RetakeBlocker(code=code, message=message, details=details)


@dataclass(frozen=True, slots=True)
class RetakeEligibility:
    """The complete answer, including the arithmetic that produced it.

    Exposing the working is the point: a learner asking "why can't I retake?" and an
    administrator asking "why does this learner have three attempts?" are answered from the same
    object, without either of them having to reconstruct the calculation.
    """

    learner_id: str
    quiz_id: str
    course_id: str | None
    state: RetakeState
    #: The single flag a caller branches on. Never derived by the client from the counts.
    can_retake: bool
    allowance: AttemptAllowance
    blockers: tuple[RetakeBlocker, ...] = field(default_factory=tuple)
    #: The completed attempt a retake would follow.
    previous_attempt_id: str | None = None
    previous_attempt_number: int | None = None
    #: The number the retake would take. Also the slot that gets reserved.
    next_attempt_number: int | None = None
    #: The version the retake would lock, resolved by the configured policy.
    configuration_version_id: str | None = None
    configuration_version_number: int | None = None
    configuration_version_source: ConfigurationVersionSource | None = None
    #: Administrator-contact guidance, present only when the allowance is spent (§13).
    guidance: str | None = None
    anomalies: tuple[RetakeAnomaly, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "quiz_id": self.quiz_id,
            "course_id": self.course_id,
            "state": self.state.value,
            "can_retake": self.can_retake,
            "allowance": self.allowance.as_dict(),
            "blockers": [item.as_dict() for item in self.blockers],
            "previous_attempt_id": self.previous_attempt_id,
            "previous_attempt_number": self.previous_attempt_number,
            "next_attempt_number": self.next_attempt_number,
            "configuration_version_id": self.configuration_version_id,
            "configuration_version_number": self.configuration_version_number,
            "configuration_version_source": (
                self.configuration_version_source.value
                if self.configuration_version_source
                else None
            ),
            "guidance": self.guidance,
            "anomalies": [item.as_dict() for item in self.anomalies],
        }


def determine_state(
    allowance: AttemptAllowance, blockers: tuple[RetakeBlocker, ...]
) -> RetakeState:
    """Reduce an allowance and a set of blockers to one headline state.

    Pure, and separated from the service that gathers the inputs, so the precedence between the
    states can be asserted directly rather than through six upstream fakes.
    """
    other_blockers = [
        item for item in blockers if item.code is not RetakeBlockerCode.NO_ATTEMPTS_REMAINING
    ]
    if other_blockers:
        return RetakeState.UNAVAILABLE
    if not allowance.has_available_attempts:
        return RetakeState.EXHAUSTED
    if allowance.relies_on_grant:
        return RetakeState.ADDITIONAL_ATTEMPT_AVAILABLE
    return RetakeState.ELIGIBLE
