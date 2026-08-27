"""Domain models. Frozen dataclasses: the domain does not mutate what it reads.

These are the platform-contract shapes. Adapters normalise upstream payloads into
them; no upstream field name, nesting or value representation appears here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from .enums import (
    GuardClass,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    ResponseMode,
    SecurityIncidentKind,
    SourceStatus,
)

#: The only origin UC-06 accepts for a case file. See assumptions row A-05.
CASE_PREP_AGENT_ORIGIN = "case_prep_agent"


@dataclass(frozen=True, slots=True)
class CaseFact:
    """A discrete fact with a stable identifier.

    The identifier is what travels into logs and interaction records. `text` is
    confidential and may be privileged: it may appear in a response to a user who
    already holds read access, and never in any log, audit or incident record.
    """

    fact_id: str
    text: str
    category: str = "general"


@dataclass(frozen=True, slots=True)
class Charge:
    charge_id: str
    label: str
    statute_reference: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    label: str
    linked_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LegislationNote:
    note_id: str
    citation: str
    summary: str


@dataclass(frozen=True, slots=True)
class CaseFile:
    """Read-only projection of a Case Prep Agent case file."""

    case_file_id: str
    origin_system: str
    practice_area: str
    charges: tuple[Charge, ...] = ()
    facts: tuple[CaseFact, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    legislation_notes: tuple[LegislationNote, ...] = ()
    source_status: SourceStatus = SourceStatus.AVAILABLE

    def fact_ids(self) -> frozenset[str]:
        return frozenset(f.fact_id for f in self.facts)

    def fact(self, fact_id: str) -> CaseFact | None:
        for f in self.facts:
            if f.fact_id == fact_id:
                return f
        return None

    @property
    def from_case_prep_agent(self) -> bool:
        return self.origin_system == CASE_PREP_AGENT_ORIGIN


@dataclass(frozen=True, slots=True)
class AccessRecord:
    """Outcome of a server-side read-access check. Never cached across requests."""

    user_id: str
    case_file_id: str
    granted: bool
    checked_at: datetime
    reason_code: str = "ok"


@dataclass(frozen=True, slots=True)
class LearnerContext:
    session_id: str
    user_id: str
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    source_status: SourceStatus
    practice_area: str | None = None
    case_linked_mode: bool = True
    case_file_id: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """What the application hands the generator. Assembled server-side only.

    `question_text` is the learner's raw question: it reaches the generator and
    nothing else. It is never logged (see docs/SHARED_CONTRACT.md, Privacy).
    """

    prompt_id: str
    prompt_version: str
    system_instructions: str
    question_text: str
    profile: str
    practice_area: str
    case_file_id: str | None
    available_fact_ids: tuple[str, ...]
    fact_digest: tuple[tuple[str, str], ...]
    charges: tuple[str, ...] = ()
    legislation: tuple[str, ...] = ()
    timeout_ms: int = 10_000


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """What a generator returns.

    `fact_ids_referenced` is the generator's own claim about which facts it used.
    It is verified against the loaded case file, never trusted. `content` may
    carry inline markers of the form [[fact:F-001]].

    `supplied_disclaimer` captures any disclaimer-looking text the generator
    emitted. It is recorded for drift detection and is NEVER used as the
    disclaimer: generated content and the disclaimer are separate fields joined
    only at the boundary.
    """

    content: str
    fact_ids_referenced: tuple[str, ...] = ()
    supplied_disclaimer: str | None = None
    model_id: str = "fake"
    prompt_version: str = "unknown"


@dataclass(frozen=True, slots=True)
class GuardResult:
    guard_class: GuardClass
    matched_rule_id: str | None = None
    topic_tag: str = "general"

    @property
    def triggered(self) -> bool:
        return self.guard_class is not GuardClass.NONE


@dataclass(frozen=True, slots=True)
class InteractionRecord:
    """The interaction log record UC-06 writes.

    There is deliberately no question_text field: a question about a live matter
    is itself sensitive. case_facts_referenced holds identifiers only.
    """

    interaction_id: str
    session_id: str
    user_id: str
    asked_at: datetime
    question_class: str
    topic_tag: str
    naric_level: NaricLevel
    response_id: str
    mode: ResponseMode
    case_file_id: str | None
    case_facts_referenced: tuple[str, ...]
    guard_triggered: GuardClass | None
    disclaimer_present: bool
    rating_state: RatingState


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Access, not content. That case-linked coaching occurred, which case file,
    which user, when. Never what was discussed."""

    audit_id: str
    occurred_at: datetime
    action: str
    user_id: str
    session_id: str
    case_file_id: str | None
    outcome: str
    source_status: SourceStatus | None = None


@dataclass(frozen=True, slots=True)
class SecurityIncident:
    incident_id: str
    occurred_at: datetime
    kind: SecurityIncidentKind
    session_id: str | None
    user_id: str | None
    case_file_id: str | None
    matched_rule_ids: tuple[str, ...] = ()
    detail_code: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AdminIncident:
    incident_id: str
    occurred_at: datetime
    severity: str
    code: str
    session_id: str | None
    user_id: str | None
    case_file_id: str | None
    technical_detail: str
    remediation: str


@dataclass(frozen=True, slots=True)
class HaltRecord:
    session_id: str
    halted: bool
    reason_code: str | None
    halted_at: datetime | None


def fact_digest(facts: Sequence[CaseFact]) -> tuple[tuple[str, str], ...]:
    return tuple((f.fact_id, f.text) for f in facts)
