"""Response emission - where layer 2 runs, and what happens when it fails.

Nothing leaves the case-coaching surface except through emit(). The sequence is
always: serialise, check, then emit - never emit then check.

On a boundary failure the behaviour is fixed and unconditional:

  1. The response is NOT emitted. The learner receives a safe error. An
     unlabelled case-linked answer reaching a practising lawyer is the outcome
     this component exists to prevent, so this path fails closed.
  2. The case-linked session is halted. Further case-linked responses in that
     session are refused until an administrator clears it.
  3. A critical defect is logged with full technical detail (identifiers only).
  4. The platform admin is alerted through AdminAlertSink.
  5. A security incident is recorded through SecurityIncidentSink, because an
     internal state in which a case-linked response was constructed without the
     disclaimer is a security event, not merely a bug.

There is no configuration key that disables, softens or bypasses any of this.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..domain.disclaimer import CANONICAL_DISCLAIMER, DISCLAIMER_FIELD
from ..domain.enums import SecurityIncidentKind
from ..domain.errors import DisclaimerBoundaryFailure
from ..domain.models import AdminIncident, SecurityIncident
from ..domain.responses import DisclaimedResponse, SafeErrorResponse
from ..logging_setup import get_logger
from ..ports.sinks import AdminAlertSink, SecurityIncidentSink
from ..ports.storage import SessionHaltRepository
from .boundary import DefaultPayloadSerializer, PayloadSerializer, check_payload

_log = get_logger("emitter")

HALT_REASON_BOUNDARY_FAILURE = "disclaimer_boundary_failure"
WITHHELD_CODE = "response_withheld"
WITHHELD_MESSAGE = (
    "This response could not be released because a required safety check did not pass. "
    "Case-linked coaching for this session is paused pending review."
)


class ResponseEmitter:
    def __init__(
        self,
        halts: SessionHaltRepository,
        admin_alerts: AdminAlertSink,
        security_incidents: SecurityIncidentSink,
        serializer: PayloadSerializer | None = None,
    ) -> None:
        self._halts = halts
        self._admin = admin_alerts
        self._security = security_incidents
        self._serializer: PayloadSerializer = serializer or DefaultPayloadSerializer()

    def emit(
        self,
        response: DisclaimedResponse,
        *,
        session_id: str,
        user_id: str,
        case_file_id: str | None,
        request_id: str,
        status_code: int = 200,
    ) -> tuple[dict[str, Any], int]:
        payload = self._serializer.serialize(response)
        try:
            check_payload(payload)
        except DisclaimerBoundaryFailure as failure:
            return self._fail_closed(
                failure,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                request_id=request_id,
            )
        return payload, status_code

    # -- fail closed ---------------------------------------------------------
    def _fail_closed(
        self,
        failure: DisclaimerBoundaryFailure,
        *,
        session_id: str,
        user_id: str,
        case_file_id: str | None,
        request_id: str,
    ) -> tuple[dict[str, Any], int]:
        incident_id = uuid4().hex
        now = datetime.now(timezone.utc)

        self._halts.halt(session_id, HALT_REASON_BOUNDARY_FAILURE)

        _log.critical(
            "disclaimer.boundary_failure",
            incident_id=incident_id,
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file_id,
            reason_code=failure.reason,
            disclaimer_present=failure.observed_present,
            severity="critical",
            halted=True,
        )

        self._admin.critical(
            AdminIncident(
                incident_id=incident_id,
                occurred_at=now,
                severity="critical",
                code=failure.reason,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                technical_detail=(
                    f"Case-linked payload failed the disclaimer boundary check at serialisation. "
                    f"reason={failure.reason}; disclaimer_field_present={failure.observed_present}; "
                    f"request_id={request_id}. Response withheld, session halted. "
                    f"No case content is included in this alert."
                ),
                remediation=(
                    "Investigate the serialisation path before clearing the halt. Clearing is an "
                    "administrative action; see docs/assumptions.md row A-06."
                ),
            )
        )

        self._security.record(
            SecurityIncident(
                incident_id=incident_id,
                occurred_at=now,
                kind=(
                    SecurityIncidentKind.INTERNAL_DISCLAIMER_ABSENT
                    if not failure.observed_present
                    else SecurityIncidentKind.INTERNAL_DISCLAIMER_ALTERED
                ),
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                matched_rule_ids=(),
                detail_code=failure.reason,
            )
        )

        safe = SafeErrorResponse(
            code=WITHHELD_CODE,
            message=WITHHELD_MESSAGE,
            request_id=request_id,
            retryable=False,
            session_halted=True,
        )
        # Build the safe error WITHOUT the injected serializer: the serializer is
        # the component just proven untrustworthy on this request.
        payload = safe.to_payload()
        try:
            check_payload(payload)
        except DisclaimerBoundaryFailure:  # pragma: no cover - literal fallback
            payload = {
                "error": {
                    "code": WITHHELD_CODE,
                    "message": WITHHELD_MESSAGE,
                    "request_id": request_id,
                    "retryable": False,
                    "session_halted": True,
                },
                DISCLAIMER_FIELD: CANONICAL_DISCLAIMER,
            }
        return payload, 503
