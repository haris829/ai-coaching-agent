"""Anomaly flags recorded on a formal attempt (§10).

A submitted formal attempt is immutable — its answers, its score and its result are facts. So when
something about it is worth an assessor's attention, UC-09 *records* it rather than correcting
anything: a second device that tried to join, a pause that was refused, an AI coaching request that
was blocked, an auto-submission that happened because the learner disconnected.

These are what the assessor's review payload surfaces under "anomaly flags". None of them is a
failure and none of them blocks anything by itself. They exist so the human making the approval
decision has the same picture the system had, and so "why was this attempt approved?" and "why was
this one escalated?" both have evidence behind them.

Anomalies are append-only and de-duplicated by code: a learner who tries a second device four times
produces one flag with an occurrence count, not four flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.modules.formal_assessment.domain.enums import (
    AnomalySeverity,
    FormalAnomalyCode,
)

#: Severity per code. WARNING is for anything that changes how the attempt should be read — the
#: learner never pressed submit, another device was involved. INFO is for refusals that worked
#: exactly as designed and are recorded for completeness.
_SEVERITY: dict[FormalAnomalyCode, AnomalySeverity] = {
    FormalAnomalyCode.AUTO_SUBMITTED_AFTER_DISCONNECT: AnomalySeverity.WARNING,
    FormalAnomalyCode.SECOND_DEVICE_ATTEMPTED: AnomalySeverity.WARNING,
    FormalAnomalyCode.PAUSE_OR_RESUME_ATTEMPTED: AnomalySeverity.INFO,
    FormalAnomalyCode.AI_COACHING_ATTEMPTED: AnomalySeverity.WARNING,
    FormalAnomalyCode.IDENTITY_CONFIRMATION_RETRIED: AnomalySeverity.INFO,
    FormalAnomalyCode.AUTOSAVE_STATE_INCOMPLETE: AnomalySeverity.WARNING,
    FormalAnomalyCode.NO_AUTOSAVED_STATE_AT_DISCONNECT: AnomalySeverity.WARNING,
    FormalAnomalyCode.UPSTREAM_STATE_MISMATCH: AnomalySeverity.WARNING,
}

_MESSAGES: dict[FormalAnomalyCode, str] = {
    FormalAnomalyCode.AUTO_SUBMITTED_AFTER_DISCONNECT: (
        "The learner's session disconnected and the assessment was submitted automatically from "
        "the answers saved up to that point."
    ),
    FormalAnomalyCode.SECOND_DEVICE_ATTEMPTED: (
        "A second device or browser session tried to open this assessment while it was in progress "
        "and was refused."
    ),
    FormalAnomalyCode.PAUSE_OR_RESUME_ATTEMPTED: (
        "A pause or resume was requested for this assessment and refused."
    ),
    FormalAnomalyCode.AI_COACHING_ATTEMPTED: (
        "AI coaching was requested while this assessment was in progress and was refused."
    ),
    FormalAnomalyCode.IDENTITY_CONFIRMATION_RETRIED: (
        "The identity confirmation was rejected at least once before it succeeded."
    ),
    FormalAnomalyCode.AUTOSAVE_STATE_INCOMPLETE: (
        "Questions were left unanswered in the state that was submitted."
    ),
    FormalAnomalyCode.NO_AUTOSAVED_STATE_AT_DISCONNECT: (
        "No autosaved answers existed when the assessment was submitted automatically."
    ),
    FormalAnomalyCode.UPSTREAM_STATE_MISMATCH: (
        "The attempt's status in the delivery module does not match this formal record's state."
    ),
}


@dataclass(frozen=True, slots=True)
class FormalAnomaly:
    """One recorded observation about a formal attempt."""

    code: FormalAnomalyCode
    severity: AnomalySeverity
    message: str
    #: How many times it happened. Kept rather than storing duplicates, so four refused devices read
    #: as "four attempts" instead of four indistinguishable flags.
    occurrences: int = 1
    first_observed_at: str | None = None
    last_observed_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "occurrences": self.occurrences,
            "first_observed_at": self.first_observed_at,
            "last_observed_at": self.last_observed_at,
            "details": dict(self.details),
        }


def anomaly(
    code: FormalAnomalyCode,
    *,
    observed_at: str | None = None,
    message: str | None = None,
    **details: Any,
) -> FormalAnomaly:
    """Build an anomaly with its standard severity and wording."""
    return FormalAnomaly(
        code=code,
        severity=_SEVERITY.get(code, AnomalySeverity.INFO),
        message=message or _MESSAGES.get(code, code.value),
        first_observed_at=observed_at,
        last_observed_at=observed_at,
        details={key: value for key, value in details.items() if value is not None},
    )


def record_anomaly(
    existing: tuple[FormalAnomaly, ...], new: FormalAnomaly
) -> tuple[FormalAnomaly, ...]:
    """Append ``new``, or increment the matching code already present.

    Order is preserved: the first observation keeps its position in the list, which is what makes a
    review payload read chronologically.
    """
    for index, current in enumerate(existing):
        if current.code is new.code:
            merged = replace(
                current,
                occurrences=current.occurrences + new.occurrences,
                last_observed_at=new.last_observed_at or current.last_observed_at,
                details={**current.details, **new.details},
            )
            return (*existing[:index], merged, *existing[index + 1 :])
    return (*existing, new)


def has_anomaly(anomalies: tuple[FormalAnomaly, ...], code: FormalAnomalyCode) -> bool:
    return any(item.code is code for item in anomalies)
