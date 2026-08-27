"""Closed vocabularies fixed by the platform contract.

Every serialised value is lowercase. Python member names are uppercase; the
value carried on the wire is not.
"""

from __future__ import annotations

from enum import Enum


class LowerStrEnum(str, Enum):
    """Base for platform enums: member name uppercase, serialised value lowercase."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class NaricLevel(LowerStrEnum):
    """Closed NARIC level enum. Never an integer scale, never a 3-point scale."""

    LEVEL_3 = "level_3"
    LEVEL_4 = "level_4"
    LEVEL_5 = "level_5"
    LEVEL_6 = "level_6"
    LEVEL_7 = "level_7"
    LEVEL_7_PLUS = "level_7_plus"


class NaricLevelSource(LowerStrEnum):
    """Where the NARIC level on a record came from."""

    RETRIEVED = "retrieved"
    DEFAULT = "default"


class ExplanationProfile(LowerStrEnum):
    """Depth of explanation applied to key concepts."""

    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class SourceStatus(LowerStrEnum):
    """Status of a data source or of a derived section.

    ``empty`` and ``unavailable`` are different states and are never conflated:
      available   - source responded and carried usable data
      empty       - source responded and legitimately carried nothing
      partial     - source responded with less than the section's target
      unavailable - source could not be reached, timed out, or errored
      invalid     - source responded with something that violates the contract
    """

    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class GenerationMode(LowerStrEnum):
    """How the summary body was produced."""

    GENERATED = "generated"
    QUESTION_LOG_FALLBACK = "question_log_fallback"


class SessionStatus(LowerStrEnum):
    """Session lifecycle values this component reads or writes.

    This component only ever *writes* ``summary_generated``; the others are
    read from the upstream session record.
    """

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    SUMMARY_GENERATED = "summary_generated"


class ResourceKind(LowerStrEnum):
    """Kind of authority cited during a session."""

    LEGISLATION = "legislation"
    CASE_LAW = "case_law"
    OTHER = "other"


class SuggestionSource(LowerStrEnum):
    """Provenance of a next-step suggestion. Both are grounded; neither is invented."""

    GAP_REPORT = "gap_report"
    SESSION_CONTENT = "session_content"


#: Section keys used in ``SummaryRecord.source_status`` alongside the source keys.
SECTION_KEYS = (
    "topics_covered",
    "key_concepts",
    "resources_referenced",
    "next_steps",
)

#: Upstream source keys used in ``SummaryRecord.source_status``.
SOURCE_KEYS = (
    "session",
    "interactions",
    "citations",
    "gap_report",
    "naric_level",
)
