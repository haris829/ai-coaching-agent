"""In-process alert and security-incident sinks.

Both keep their records in memory and also emit a structured log line through the
sanitising logger, so an operator sees them without a second system - and so the
privacy test can prove that neither sink leaks case fact text or question text.
"""

from __future__ import annotations

from threading import RLock
from typing import Sequence

from ...config import Settings
from ...domain.models import AdminIncident, SecurityIncident
from ...logging_setup import get_logger

_log = get_logger("sinks")


class InMemoryAdminAlertSink:
    """Implements AdminAlertSink."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._lock = RLock()
        self._incidents: list[AdminIncident] = []

    def critical(self, incident: AdminIncident) -> None:
        with self._lock:
            self._incidents.append(incident)
        # technical_detail is deliberately NOT logged: it is for the responder in
        # the alert channel, and it is composed to contain identifiers only.
        _log.critical(
            "disclaimer.boundary_failure",
            incident_id=incident.incident_id,
            severity=incident.severity,
            error_code=incident.code,
            session_id=incident.session_id,
            user_id=incident.user_id,
            case_file_id=incident.case_file_id,
        )

    def incidents(self) -> Sequence[AdminIncident]:
        with self._lock:
            return tuple(self._incidents)


class InMemorySecurityIncidentSink:
    """Implements SecurityIncidentSink.

    Distinct from application logging on purpose: a suppression attempt is a
    security event with its own review path, not a warning line in request logs.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._lock = RLock()
        self._incidents: list[SecurityIncident] = []

    def record(self, incident: SecurityIncident) -> None:
        with self._lock:
            self._incidents.append(incident)
        _log.warning(
            "security.incident_recorded",
            incident_id=incident.incident_id,
            kind=incident.kind.value,
            session_id=incident.session_id,
            user_id=incident.user_id,
            case_file_id=incident.case_file_id,
            matched_rule_ids=list(incident.matched_rule_ids),
            detail_code=incident.detail_code,
        )

    def incidents(self) -> Sequence[SecurityIncident]:
        with self._lock:
            return tuple(self._incidents)
