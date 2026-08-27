"""ForeignCaseFileAdapter - the case-file adapter for the fictional Mattersphere.

Written the way a company engineer would write theirs from _template.py. It is
the only place Mattersphere's field names, nesting, verdict strings and exception
type are known. Nothing above it changes because this exists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...config import Settings
from ...domain.enums import SourceStatus
from ...domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from ...domain.models import (
    CASE_PREP_AGENT_ORIGIN,
    AccessRecord,
    CaseFact,
    CaseFile,
    Charge,
    EvidenceItem,
    LegislationNote,
)
from . import _upstream

PORT_NAME = "case_file_provider"

#: What "originated from the Case Prep Agent" is verified against for this
#: upstream. Checked against the real system before release - assumptions A-05.
CASE_PREP_PRODUCERS = frozenset({"casePrepAgent/v3", "casePrepAgent/v2"})


#: Conformance scenario map, declared in the adapter (see tests/conformance).
CONFORMANCE_SCENARIOS: dict[str, str] = {
    "readable": _upstream.MATTER_STANDARD,
    "partial": _upstream.MATTER_NO_AUTHORITIES,
    "empty": _upstream.MATTER_EMPTY,
    "access_denied": _upstream.MATTER_BLOCKED,
    "foreign_origin": _upstream.MATTER_OTHER_ORIGIN,
    "unavailable": _upstream.MATTER_GONE,
    "invalid": _upstream.MATTER_GARBLED,
    "timeout": _upstream.MATTER_SLOW,
}


class ForeignCaseFileAdapter:
    """Implements CaseFileProvider. READ ONLY."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def verify_read_access(self, user_id: str, case_file_id: str) -> AccessRecord:
        try:
            raw = _upstream.fetch_permission(user_id, case_file_id)
        except _upstream.MatterSphereError as exc:
            # The upstream error text mentions the vendor and its internals. It
            # stops here.
            raise ProviderUnavailable(PORT_NAME, "access_service_unreachable") from exc
        except TimeoutError as exc:
            raise ProviderTimeout(PORT_NAME, "access_check_timeout") from exc

        decision = _dig(raw, "envelope", "decision")
        verdict = decision.get("verdict") if isinstance(decision, dict) else None
        if verdict not in {"PERMIT", "DENY"}:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_access_decision")
        return AccessRecord(
            user_id=user_id,
            case_file_id=case_file_id,
            granted=verdict == "PERMIT",
            checked_at=datetime.now(timezone.utc),
            reason_code="ok" if verdict == "PERMIT" else "not_on_matter",
        )

    def get_case_file(self, case_file_id: str) -> CaseFile:
        try:
            raw = _upstream.fetch_matter(case_file_id)
        except _upstream.MatterSphereError as exc:
            raise ProviderUnavailable(PORT_NAME, "case_service_unreachable") from exc
        except TimeoutError as exc:
            raise ProviderTimeout(PORT_NAME, "case_read_timeout") from exc

        record = _dig(raw, "envelope", "record")
        if not isinstance(record, dict):
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_case_payload")

        try:
            facts = tuple(
                CaseFact(
                    fact_id=str(item["ref"]),
                    text=str(item["narrative"]),
                    category=str(item.get("kind", "general")).lower(),
                )
                for item in record.get("particulars", [])
            )
            charges = tuple(
                Charge(str(c["countRef"]), str(c["descriptor"]), c.get("provision"))
                for c in record.get("counts", [])
            )
            evidence = tuple(
                EvidenceItem(
                    str(e["exhRef"]),
                    str(e["descriptor"]),
                    tuple(str(r) for r in e.get("particularRefs", [])),
                )
                for e in record.get("exhibits", [])
            )
            notes = tuple(
                LegislationNote(str(a["authRef"]), str(a["cite"]), str(a.get("headnote", "")))
                for a in record.get("authorities", [])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_case_payload") from exc

        producer = _dig(record, "provenance", "producedBy")
        origin = CASE_PREP_AGENT_ORIGIN if producer in CASE_PREP_PRODUCERS else "unknown"

        if not facts and not charges:
            status = SourceStatus.EMPTY
        elif not notes:
            status = SourceStatus.PARTIAL
        else:
            status = SourceStatus.AVAILABLE

        return CaseFile(
            case_file_id=case_file_id,
            origin_system=origin,
            practice_area=str(record.get("practiceGroup", "unknown")).lower(),
            charges=charges,
            facts=facts,
            evidence=evidence,
            legislation_notes=notes,
            source_status=status,
        )


def _dig(payload: Any, *path: str) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
