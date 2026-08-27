"""MockCaseFileProvider - deterministic case-file scenarios.

Scenario selection is by case_file_id. No randomness, no sleeps, no clock
dependence: the same id always produces the same outcome.

READ ONLY. This adapter exposes exactly the two port methods plus test-visible
counters. It has no create, update, delete, patch or write method, and
tests/test_readonly_architecture.py asserts that.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

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

# Scenario identifiers. Stable, documented in docs/SHARED_CONTRACT.md.
CASE_FULL: Final = "CASE-FULL-001"
CASE_SPARSE: Final = "CASE-SPARSE-002"
CASE_NO_LEGISLATION: Final = "CASE-NOLEG-003"
CASE_ACCESS_DENIED: Final = "CASE-DENIED-004"
CASE_FOREIGN_ORIGIN: Final = "CASE-FOREIGN-005"
CASE_UNAVAILABLE: Final = "CASE-UNAVAILABLE-006"
CASE_INVALID_SHAPE: Final = "CASE-INVALID-007"
CASE_TIMEOUT: Final = "CASE-TIMEOUT-008"
CASE_EMPTY_FACTS: Final = "CASE-EMPTY-009"
CASE_CIVIL: Final = "CASE-CIVIL-010"

#: The user id the denial scenario denies. Any other user is granted, except on
#: CASE_ACCESS_DENIED which denies everyone.
OTHER_USER: Final = "user-not-on-matter"


def _full_case() -> CaseFile:
    return CaseFile(
        case_file_id=CASE_FULL,
        origin_system=CASE_PREP_AGENT_ORIGIN,
        practice_area="criminal",
        charges=(
            Charge("CH-001", "Robbery, contrary to section 8 Theft Act 1968", "Theft Act 1968, s.8"),
            Charge("CH-002", "Possession of an offensive weapon", "Prevention of Crime Act 1953, s.1"),
        ),
        facts=(
            CaseFact(
                "F-001",
                "The defendant states that two men approached him outside the depot on 14 March and "
                "said his brother would be hurt that night unless he opened the rear gate.",
                "defendant_account",
            ),
            CaseFact(
                "F-002",
                "CCTV timed at 23:41 shows the rear gate being opened from the inside by a person in "
                "a light hooded jacket.",
                "evidence_summary",
            ),
            CaseFact(
                "F-003",
                "The defendant did not contact the police between the alleged threat and the opening "
                "of the gate, a period of about six hours.",
                "chronology",
            ),
            CaseFact(
                "F-004",
                "The defendant had exchanged messages with one of the two men on four occasions in "
                "the preceding fortnight.",
                "communications",
            ),
            CaseFact(
                "F-005",
                "A knife was recovered from the footwell of the vehicle the defendant travelled in.",
                "evidence_summary",
            ),
        ),
        evidence=(
            EvidenceItem("EV-001", "CCTV export, rear gate camera", ("F-002",)),
            EvidenceItem("EV-002", "Cell site and message download", ("F-004",)),
            EvidenceItem("EV-003", "Exhibit: folding knife", ("F-005",)),
        ),
        legislation_notes=(
            LegislationNote("LN-001", "Theft Act 1968, s.8", "Robbery: theft with force or threat of force."),
            LegislationNote(
                "LN-002",
                "R v Hasan [2005] UKHL 22",
                "Duress: voluntary association with those making the threat forecloses the defence.",
            ),
        ),
        source_status=SourceStatus.AVAILABLE,
    )


def _sparse_case() -> CaseFile:
    return CaseFile(
        case_file_id=CASE_SPARSE,
        origin_system=CASE_PREP_AGENT_ORIGIN,
        practice_area="criminal",
        charges=(Charge("CH-101", "Theft, contrary to section 1 Theft Act 1968", "Theft Act 1968, s.1"),),
        facts=(
            CaseFact("F-101", "The item was taken from the shelf and carried past the till.", "chronology"),
        ),
        evidence=(),
        legislation_notes=(LegislationNote("LN-101", "Theft Act 1968, s.1", "Dishonest appropriation."),),
        source_status=SourceStatus.PARTIAL,
    )


def _no_legislation_case() -> CaseFile:
    base = _full_case()
    return CaseFile(
        case_file_id=CASE_NO_LEGISLATION,
        origin_system=base.origin_system,
        practice_area=base.practice_area,
        charges=base.charges,
        facts=base.facts[:3],
        evidence=base.evidence[:1],
        legislation_notes=(),
        source_status=SourceStatus.PARTIAL,
    )


def _empty_facts_case() -> CaseFile:
    """`empty` is not `unavailable`: the source answered and legitimately held
    no facts yet."""
    return CaseFile(
        case_file_id=CASE_EMPTY_FACTS,
        origin_system=CASE_PREP_AGENT_ORIGIN,
        practice_area="criminal",
        charges=(),
        facts=(),
        evidence=(),
        legislation_notes=(),
        source_status=SourceStatus.EMPTY,
    )


def _foreign_origin_case() -> CaseFile:
    base = _full_case()
    return CaseFile(
        case_file_id=CASE_FOREIGN_ORIGIN,
        origin_system="third_party_import",
        practice_area=base.practice_area,
        charges=base.charges,
        facts=base.facts,
        evidence=base.evidence,
        legislation_notes=base.legislation_notes,
        source_status=SourceStatus.AVAILABLE,
    )


def _civil_case() -> CaseFile:
    return CaseFile(
        case_file_id=CASE_CIVIL,
        origin_system=CASE_PREP_AGENT_ORIGIN,
        practice_area="civil litigation",
        charges=(Charge("CH-201", "Claim in negligence", None),),
        facts=(
            CaseFact("F-201", "The walkway had no handrail on the north side at the time of the fall.", "site"),
            CaseFact("F-202", "An inspection log records the last check as eleven weeks earlier.", "records"),
            CaseFact("F-203", "The claimant was carrying a stacked tray at the time.", "claimant_account"),
        ),
        evidence=(EvidenceItem("EV-201", "Site photographs", ("F-201",)),),
        legislation_notes=(
            LegislationNote("LN-201", "Occupiers' Liability Act 1957, s.2", "Common duty of care."),
        ),
        source_status=SourceStatus.AVAILABLE,
    )


_CASES = {
    CASE_FULL: _full_case,
    CASE_SPARSE: _sparse_case,
    CASE_NO_LEGISLATION: _no_legislation_case,
    CASE_ACCESS_DENIED: _full_case,
    CASE_FOREIGN_ORIGIN: _foreign_origin_case,
    CASE_EMPTY_FACTS: _empty_facts_case,
    CASE_CIVIL: _civil_case,
}


#: Which identifier exercises which contract case, for the conformance kit
#: (tests/conformance). Declared HERE, in the adapter, so registering a new
#: adapter never requires editing a test file.
CONFORMANCE_SCENARIOS: Final[dict[str, str]] = {
    "readable": CASE_FULL,
    "partial": CASE_NO_LEGISLATION,
    "empty": CASE_EMPTY_FACTS,
    "access_denied": CASE_ACCESS_DENIED,
    "foreign_origin": CASE_FOREIGN_ORIGIN,
    "unavailable": CASE_UNAVAILABLE,
    "invalid": CASE_INVALID_SHAPE,
    "timeout": CASE_TIMEOUT,
}


class MockCaseFileProvider:
    """Implements CaseFileProvider. Read operations only."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        #: Test-visible call counters. Reads of adapter behaviour, not case data.
        self.access_checks: list[tuple[str, str]] = []
        self.reads: list[str] = []

    # -- port ---------------------------------------------------------------
    def verify_read_access(self, user_id: str, case_file_id: str) -> AccessRecord:
        self.access_checks.append((user_id, case_file_id))
        if case_file_id == CASE_UNAVAILABLE:
            raise ProviderUnavailable("case_file_provider", "access_service_unreachable")
        if case_file_id == CASE_TIMEOUT:
            raise ProviderTimeout("case_file_provider", "access_check_timeout")
        granted = case_file_id != CASE_ACCESS_DENIED and user_id != OTHER_USER
        return AccessRecord(
            user_id=user_id,
            case_file_id=case_file_id,
            granted=granted,
            checked_at=datetime.now(timezone.utc),
            reason_code="ok" if granted else "not_on_matter",
        )

    def get_case_file(self, case_file_id: str) -> CaseFile:
        self.reads.append(case_file_id)
        if case_file_id == CASE_UNAVAILABLE:
            raise ProviderUnavailable("case_file_provider", "case_service_unreachable")
        if case_file_id == CASE_TIMEOUT:
            raise ProviderTimeout("case_file_provider", "case_read_timeout")
        if case_file_id == CASE_INVALID_SHAPE:
            raise ProviderInvalidResponse("case_file_provider", "unmappable_case_payload")
        builder = _CASES.get(case_file_id)
        if builder is None:
            raise ProviderInvalidResponse("case_file_provider", "unknown_case_file_id")
        return builder()
