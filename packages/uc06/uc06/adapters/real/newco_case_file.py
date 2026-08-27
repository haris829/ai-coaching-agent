"""NewCo case file adapter - produced from _template.py in the swap demonstration.

A third payload shape, unrelated to both the mock and the foreign family: flat
top level, arrays under different names again, a numeric permission code, and
integer fact keys that must be stringified.
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

PORT_NAME = "case_file_provider"
CASE_PREP_PRODUCER_CODES = frozenset({7, 8})

_FIXTURES: dict[str, dict[str, Any]] = {
    "NC-1": {
        "dossier_no": "NC-1",
        "producer_code": 7,
        "area_of_law": "Criminal",
        "counts_list": [{"n": 1, "wording": "Robbery", "sec": "Theft Act 1968 s.8"}],
        "points": [
            {"key": 101, "body": "The client says he was threatened outside the depot on 14 March."},
            {"key": 102, "body": "Gate camera timestamps the opening at 23:41."},
        ],
        "docs": [{"doc_no": 5, "caption": "Camera export", "point_keys": [102]}],
        "refs": [{"ref_no": 9, "cited_as": "Theft Act 1968 s.8", "gist": "Robbery."}],
    },
    "NC-2": {  # partial: no legislation refs
        "dossier_no": "NC-2",
        "producer_code": 8,
        "area_of_law": "Criminal",
        "counts_list": [{"n": 1, "wording": "Theft", "sec": "Theft Act 1968 s.1"}],
        "points": [{"key": 201, "body": "The item was carried past the till."}],
        "docs": [],
        "refs": [],
    },
    "NC-3": {  # empty: answered, and legitimately holds nothing
        "dossier_no": "NC-3",
        "producer_code": 7,
        "area_of_law": "Criminal",
        "counts_list": [],
        "points": [],
        "docs": [],
        "refs": [],
    },
    "NC-4": {**{}, "dossier_no": "NC-4", "producer_code": 99, "area_of_law": "Criminal",
             "counts_list": [], "points": [{"key": 401, "body": "Imported from a bulk load."}],
             "docs": [], "refs": []},  # not from the Case Prep Agent
    "NC-BAD": {"dossier_no": "NC-BAD", "producer_code": 7, "area_of_law": "Criminal",
               "counts_list": [], "points": [{"no_key_field": True}], "docs": [], "refs": []},
}

_DENIED = "NC-DENIED"
_GONE = "NC-GONE"
_SLOW = "NC-SLOW"


#: Conformance scenario map, declared in the adapter. This is the whole cost of
#: pointing the conformance kit at a new adapter: no test file is edited.
CONFORMANCE_SCENARIOS: dict[str, str] = {
    "readable": "NC-1",
    "partial": "NC-2",
    "empty": "NC-3",
    "access_denied": _DENIED,
    "foreign_origin": "NC-4",
    "unavailable": _GONE,
    "invalid": "NC-BAD",
    "timeout": _SLOW,
}


class NewCoCaseFileAdapter:
    """Implements CaseFileProvider. READ ONLY."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def verify_read_access(self, user_id: str, case_file_id: str) -> AccessRecord:
        raw = self._fetch_permission(case_file_id)
        code = raw.get("permit_code")
        if code not in {0, 1}:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_access_decision")
        granted = code == 1
        return AccessRecord(
            user_id=user_id,
            case_file_id=case_file_id,
            granted=granted,
            checked_at=datetime.now(timezone.utc),
            reason_code="ok" if granted else "not_on_matter",
        )

    def get_case_file(self, case_file_id: str) -> CaseFile:
        raw = self._fetch(case_file_id)
        try:
            facts = tuple(
                CaseFact(str(point["key"]), str(point["body"])) for point in raw["points"]
            )
            charges = tuple(
                Charge(str(count["n"]), str(count["wording"]), count.get("sec"))
                for count in raw["counts_list"]
            )
            evidence = tuple(
                EvidenceItem(
                    str(doc["doc_no"]),
                    str(doc["caption"]),
                    tuple(str(k) for k in doc.get("point_keys", [])),
                )
                for doc in raw["docs"]
            )
            notes = tuple(
                LegislationNote(str(ref["ref_no"]), str(ref["cited_as"]), str(ref.get("gist", "")))
                for ref in raw["refs"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_case_payload") from exc

        origin = (
            CASE_PREP_AGENT_ORIGIN if raw.get("producer_code") in CASE_PREP_PRODUCER_CODES else "unknown"
        )
        if not facts and not charges:
            status = SourceStatus.EMPTY
        elif not notes:
            status = SourceStatus.PARTIAL
        else:
            status = SourceStatus.AVAILABLE

        return CaseFile(
            case_file_id=case_file_id,
            origin_system=origin,
            practice_area=str(raw.get("area_of_law", "unknown")).lower(),
            charges=charges,
            facts=facts,
            evidence=evidence,
            legislation_notes=notes,
            source_status=status,
        )

    # -- transport (stubbed for the demonstration) --------------------------
    def _fetch(self, case_file_id: str) -> dict[str, Any]:
        if case_file_id in {_GONE, _DENIED}:
            raise ProviderUnavailable(PORT_NAME, "case_service_unreachable")
        if case_file_id == _SLOW:
            raise ProviderTimeout(PORT_NAME, "case_read_timeout")
        payload = _FIXTURES.get(case_file_id)
        if payload is None:
            raise ProviderInvalidResponse(PORT_NAME, "unknown_case_file_id")
        return payload

    def _fetch_permission(self, case_file_id: str) -> dict[str, Any]:
        if case_file_id == _GONE:
            raise ProviderUnavailable(PORT_NAME, "access_service_unreachable")
        if case_file_id == _SLOW:
            raise ProviderTimeout(PORT_NAME, "access_check_timeout")
        return {"permit_code": 0 if case_file_id == _DENIED else 1}
