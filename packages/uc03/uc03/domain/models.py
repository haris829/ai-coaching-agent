"""Domain models: the UC-03 response contract and the internal value objects
passed across the internal contracts.

Everything here is pydantic v2 so the API layer can serialise it directly and
so validation happens once, at the boundary of the core.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    ALL_FOLLOW_UP_ACTIONS,
    DEFAULT_NARIC_LEVEL,
    AuthorityStatus,
    Classification,
    ClassificationKind,
    ExplanationDepth,
    FieldAvailability,
    FollowUpAction,
    FramingStrategy,
    LogStatus,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    ResponseStatus,
)
from .topics import TopicTag


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------
# Context (produced by ContextProvider — never by the client)
# --------------------------------------------------------------------------


class LearnerContext(_Frozen):
    """Normalised learner context.

    `naric_level` always carries a real qualification level; `naric_level_source`
    says whether that level was retrieved or defaulted. The level is therefore
    never ambiguous to a reader, and "unknown" is not representable as a level.

    `practice_area` uses the `FieldAvailability` vocabulary because it has a
    third genuine state (the learner has no recorded speciality) that the
    retrieved/default pair cannot express.
    """

    user_id: str
    session_id: str
    naric_level: NaricLevel = DEFAULT_NARIC_LEVEL
    naric_level_source: NaricLevelSource = NaricLevelSource.DEFAULT
    practice_area: str | None = None
    practice_area_availability: FieldAvailability = FieldAvailability.MISSING

    @property
    def has_naric(self) -> bool:
        return self.naric_level_source is NaricLevelSource.RETRIEVED

    @property
    def has_practice_area(self) -> bool:
        return (
            self.practice_area_availability is FieldAvailability.PROVIDED
            and bool(self.practice_area)
        )


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


class ClassificationResult(_Frozen):
    kind: ClassificationKind
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    # Populated only when kind is AMBIGUOUS. Exactly one question, by contract.
    clarification_question: str | None = None
    rationale: str | None = None


# --------------------------------------------------------------------------
# Legal authority
# --------------------------------------------------------------------------


class VerifiedAuthority(_Frozen):
    """A citation that the authority source has affirmatively verified.

    `verified_by` and `verification_id` exist so an auditor can trace any
    citation UC-03 emitted back to the source that vouched for it. An LLM
    cannot construct this object — only a LegalAuthorityProvider can.
    """

    citation: str
    title: str
    source: str
    url: str | None = None
    verified_by: str
    verification_id: str
    retrieved_at: datetime


class AuthorityPart(_Frozen):
    """The authority section of the four-part answer."""

    status: AuthorityStatus
    authority: VerifiedAuthority | None = None
    message: str | None = None
    verification_routes: tuple[str, ...] = ()

    @property
    def is_verified(self) -> bool:
        return self.status is AuthorityStatus.VERIFIED and self.authority is not None


class AuthorityLookupResult(_Frozen):
    """What a LegalAuthorityProvider returns."""

    status: AuthorityStatus
    authority: VerifiedAuthority | None = None


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


class GenerationRequest(_Frozen):
    """Input to the AnswerGenerator.

    Deliberately carries no authority data: the generator is structurally
    incapable of writing the Authority Reference section, which is assembled by
    the service from the LegalAuthorityProvider alone.

    `framing` names the explanation strategy to use. The service selects it from
    the framings not yet used for this concept in this session.
    """

    question: str
    classification: Classification
    depth: ExplanationDepth
    practice_area: str | None
    practice_area_available: bool
    framing: FramingStrategy = FramingStrategy.FIRST_PRINCIPLES


class GeneratedProse(_Frozen):
    """The three prose parts. The fourth part never comes from here."""

    plain_english: str
    formal_definition: str
    practice_example: str


class AnswerParts(_Frozen):
    """The four-part answer, kept as separate fields so a future frontend can
    render each as its own visually distinct section."""

    plain_english: str
    formal_definition: str
    practice_example: str
    authority: AuthorityPart


# --------------------------------------------------------------------------
# Response contract
# --------------------------------------------------------------------------


class ResponseMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elapsed_ms: int
    thinking_after_ms: int
    timeout_ms: int
    thinking_state_emitted: bool = False
    explanation_depth: ExplanationDepth | None = None
    naric_level: NaricLevel | None = None
    naric_level_source: NaricLevelSource | None = None
    practice_area_availability: FieldAvailability = FieldAvailability.MISSING
    personalisation_applied: bool = False
    topic_tag: TopicTag = TopicTag.UNCLASSIFIED
    topic_tag_accepted: bool = False
    framing: FramingStrategy | None = None
    framings_used: tuple[FramingStrategy, ...] = ()
    framings_remaining: int | None = None
    citation_guard_violations: int = 0
    log_status: LogStatus = LogStatus.RECORDED
    degraded: tuple[str, ...] = ()


class QuestionResponse(BaseModel):
    """The UC-03 wire contract."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    session_id: str
    classification: Classification | None = None
    status: ResponseStatus
    parts: AnswerParts | None = None
    clarification_question: str | None = None
    message: str | None = None
    follow_up_actions: tuple[FollowUpAction, ...] = ()
    rating_state: RatingState = RatingState.PENDING
    retry_available: bool = False
    #: Set on a follow-up response: the question_id of the interaction this
    #: elaborates on.
    follow_up_of: str | None = None
    meta: ResponseMeta

    @property
    def is_answered(self) -> bool:
        return self.status is ResponseStatus.ANSWERED


def default_follow_up_actions() -> tuple[FollowUpAction, ...]:
    return ALL_FOLLOW_UP_ACTIONS


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


class QuestionLogRecord(BaseModel):
    """One row per incoming question — successes, clarifications, out-of-scope,
    errors and timeouts alike. `answer` is None when no answer exists; the
    status field carries why, so nothing pretends an answer was produced."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    session_id: str
    user_id: str
    question: str
    classification: ClassificationKind | None
    status: ResponseStatus
    answer: AnswerParts | None
    topic_tag: TopicTag
    topic_tag_accepted: bool
    timestamp: datetime
    rating_state: RatingState = RatingState.PENDING
    #: Qualification level the answer was pitched at, and whether that level was
    #: retrieved or defaulted. Recorded so a reader can tell the difference.
    naric_level: NaricLevel | None = None
    naric_level_source: NaricLevelSource | None = None
    #: Concept key and framing, for the never-repeat-a-framing rule.
    concept_key: str | None = None
    framing: FramingStrategy | None = None
    #: question_id of the interaction this follow-up elaborates on.
    follow_up_of: str | None = None
    follow_up_action: FollowUpAction | None = None
    elapsed_ms: int = 0
    citation_guard_violations: int = 0
    degraded: tuple[str, ...] = ()
    error: str | None = None


class Principal(_Frozen):
    """Authenticated caller, resolved server-side from the credential."""

    user_id: str
