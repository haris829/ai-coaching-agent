"""Closed enumerations for UC-03. Every value here is part of the wire contract.

Casing follows the platform contract:
  * every value here is lowercase, per PLATFORM_CONTRACT.md §7
  * except `NaricLevel`, which stays UPPERCASE - see the warning below

The lowercase rename was instructed by integration brief §4.2 and covers
`Classification`, `ClassificationKind`, `ResponseStatus`, `FollowUpAction`,
`AuthorityStatus`, `ExplanationDepth`, `FieldAvailability` and `LogStatus`.

MIGRATION WARNING
    These values are PERSISTED in `QuestionLogRecord` (status, classification,
    follow_up_action) and were UPPERCASE before this rename. Historical rows
    written by an earlier build carry the old casing and will not parse against
    these enums. A data migration must run before this build reads any store
    that predates it. Nothing in this package performs one.

NaricLevel is deliberately excluded from the rename. Brief §4.2 does not list
it, and it is the open platform-wide row: UC-03 through UC-08 emit `LEVEL_5`
while UC-09 and UC-10 emit `level_5`. Changing it here would pick an answer to
a question the company still owns (PLATFORM_CONTRACT.md §2), so it is left as
it was and the divergence stays visible.
"""

from enum import Enum


class Classification(str, Enum):
    """The three company-mandated question classes."""

    LEGAL_CONCEPT = "legal_concept"
    PROCESS = "process"
    DEFINITIONAL = "definitional"


class ClassificationKind(str, Enum):
    """Full classifier outcome space.

    The three company classes plus the two non-answering outcomes. Only the
    three `Classification` members ever reach the ``classification`` field of an
    ANSWERED response; AMBIGUOUS and OUT_OF_SCOPE short-circuit generation.
    """

    LEGAL_CONCEPT = "legal_concept"
    PROCESS = "process"
    DEFINITIONAL = "definitional"
    AMBIGUOUS = "ambiguous"
    OUT_OF_SCOPE = "out_of_scope"

    def as_classification(self) -> "Classification | None":
        try:
            return Classification(self.value)
        except ValueError:
            return None


class ResponseStatus(str, Enum):
    ANSWERED = "answered"
    CLARIFICATION_NEEDED = "clarification_needed"
    OUT_OF_SCOPE = "out_of_scope"
    TIMEOUT = "timeout"
    ERROR = "error"
    # Every distinct framing for this concept has been used in this session.
    # UC-03 says so rather than silently cycling back to the first framing.
    FRAMINGS_EXHAUSTED = "framings_exhausted"


class RatingState(str, Enum):
    """Platform contract: `rating_state` of `pending` or `rated`.

    UC-03 only ever emits `pending`; UC-10 owns the transition to `rated`.
    """

    PENDING = "pending"
    RATED = "rated"


class FollowUpAction(str, Enum):
    EXPLAIN_DIFFERENTLY = "explain_differently"
    ANOTHER_EXAMPLE = "another_example"
    GO_DEEPER = "go_deeper"


ALL_FOLLOW_UP_ACTIONS: tuple[FollowUpAction, ...] = (
    FollowUpAction.EXPLAIN_DIFFERENTLY,
    FollowUpAction.ANOTHER_EXAMPLE,
    FollowUpAction.GO_DEEPER,
)


class FramingStrategy(str, Enum):
    """Distinct ways of explaining the same concept.

    A follow-up must select a framing not already used for that concept in that
    session. A paraphrase of an earlier framing is a repeat, not a new framing.
    """

    ANALOGY = "analogy"
    WORKED_EXAMPLE = "worked_example"
    CONTRAST_NEAR_MISS = "contrast_near_miss"
    FIRST_PRINCIPLES = "first_principles"
    PROCEDURAL_WALKTHROUGH = "procedural_walkthrough"
    MISCONCEPTION_CORRECTION = "misconception_correction"


ALL_FRAMINGS: tuple[FramingStrategy, ...] = tuple(FramingStrategy)


class AuthorityStatus(str, Enum):
    VERIFIED = "verified"
    NO_VERIFIED_AUTHORITY = "no_verified_authority"


class NaricLevel(str, Enum):
    """UK ENIC/NARIC comparability level of the learner's qualification.

    Closed set, per the platform contract. There is deliberately no UNKNOWN
    member: "we do not know the level" is carried by `naric_level_source`, not
    by an in-band level value, so a stored level is never ambiguous.
    """

    LEVEL_3 = "LEVEL_3"
    LEVEL_4 = "LEVEL_4"
    LEVEL_5 = "LEVEL_5"
    LEVEL_6 = "LEVEL_6"  # bachelor's-degree comparable
    LEVEL_7 = "LEVEL_7"  # master's comparable
    LEVEL_7_PLUS = "LEVEL_7_PLUS"  # above master's comparable


class NaricLevelSource(str, Enum):
    """Whether the qualification level was retrieved or defaulted."""

    RETRIEVED = "retrieved"
    DEFAULT = "default"


#: Applied when no level was retrieved. The most accessible level, so an
#: unknown learner is never assumed to be an expert.
DEFAULT_NARIC_LEVEL: "NaricLevel" = NaricLevel.LEVEL_3


class ExplanationDepth(str, Enum):
    """Deterministic explanation profile derived from NARIC level."""

    FOUNDATION = "foundation"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class FieldAvailability(str, Enum):
    """Provenance of a context field other than the qualification level."""

    PROVIDED = "provided"
    MISSING = "missing"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class LogStatus(str, Enum):
    RECORDED = "recorded"
    FAILED = "failed"
