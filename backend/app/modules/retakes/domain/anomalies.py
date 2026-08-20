"""Anomalies — things worth recording that are not failures.

A retake that has already been created must never be destroyed to report a problem with it. Once
UC-03 has delivered an attempt, that attempt is real, the learner can sit it, and deleting it to
signal a defect would be worse than the defect. So anything UC-08 notices after the point of no
return is recorded on the retake and surfaced in the response instead of being raised.

The same shape as UC-05's ``ResultAnomaly``, deliberately: a caller that already renders one can
render the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.retakes.domain.enums import AnomalySeverity, RetakeAnomalyCode


@dataclass(frozen=True, slots=True)
class RetakeAnomaly:
    code: RetakeAnomalyCode
    severity: AnomalySeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
        }


def anomaly(
    code: RetakeAnomalyCode,
    message: str,
    *,
    severity: AnomalySeverity = AnomalySeverity.WARNING,
    **details: Any,
) -> RetakeAnomaly:
    return RetakeAnomaly(code=code, severity=severity, message=message, details=details)
