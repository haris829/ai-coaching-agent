"""Alerting and security-incident ports.

These are separate ports on purpose. A security incident is not an application
log line and is not an admin alert: it is a distinct record with a distinct
retention and review path, and conflating them is how suppression attempts get
lost in log volume.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import AdminIncident, SecurityIncident


@runtime_checkable
class AdminAlertSink(Protocol):
    """Immediate notification to the platform admin.

    critical() is called when a case-linked payload fails the disclaimer boundary
    check. The incident carries full technical detail for the responder and no
    case content.
    """

    def critical(self, incident: AdminIncident) -> None:
        ...


@runtime_checkable
class SecurityIncidentSink(Protocol):
    """Records attempts - prompt-based or technical - to suppress the disclaimer,
    and unauthorised case access.

    The record carries the classification and the matched rule identifiers. It
    never carries the question text or any case fact text.
    """

    def record(self, incident: SecurityIncident) -> None:
        ...
