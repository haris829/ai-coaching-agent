"""Domain models.

These are the shapes UC-05 owns.  Anything marked SPECIFIED is fixed by the
platform contract and must not be renamed; anything marked ASSUMED has a row
in ``docs/assumptions.md``.

All models forbid unknown fields.  That is deliberate at the *domain* boundary
too, not only at the API: an adapter that quietly passes an upstream field
through would otherwise leak an upstream shape into the domain, which is
exactly what the integration rule forbids.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    DialogueState,
    ExplanationProfile,
    IntentKind,
    Mode,
    ModeSource,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    Resolution,
    ResponseKind,
    SourceStatus,
)
from .profiles import explanation_profile_for


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------
# Inbound from ports
# --------------------------------------------------------------------------


class LearnerContext(_Strict):
    """SPECIFIED shape (section 6).  UC-05 receives this; it never assembles it.

    ``source_status`` is keyed by the logical source name.  UC-05 reads
    ``naric_level`` and ``practice_area``; unknown keys are permitted in the
    map because other components own other sources.
    """

    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    practice_area: str | None = None
    source_status: dict[str, SourceStatus] = Field(default_factory=dict)

    @property
    def explanation_profile(self) -> ExplanationProfile:
        return explanation_profile_for(self.naric_level)

    @classmethod
    def defaulted(cls, reason: SourceStatus) -> "LearnerContext":
        """The context used when the provider could not supply one.

        Level 5, source ``default``, general examples, status recorded -- and
        the dialogue proceeds.  A context failure never leaves the learner
        without a response.
        """
        return cls(
            naric_level=NaricLevel.LEVEL_5,
            naric_level_source=NaricLevelSource.DEFAULT,
            practice_area=None,
            source_status={"naric_level": reason, "practice_area": reason},
        )


class FourPartAnswer(_Strict):
    """SPECIFIED shape.  The platform's four-part structure, discrete fields.

    Every part is required and must be non-blank.  A generator that omits one
    has produced a ``ProviderInvalidResponse``; there is no partial answer.
    """

    plain_english_explanation: str
    formal_legal_definition: str
    practical_example: str
    authority_reference: str

    @field_validator(
        "plain_english_explanation",
        "formal_legal_definition",
        "practical_example",
        "authority_reference",
    )
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("four-part answer parts must all be non-blank")
        return value


class GuidingQuestionResult(_Strict):
    """ASSUMED shape (A-GQ-RESULT).

    ``probing_focus`` is what makes the cap's reasoning chain assemblable from
    the record rather than regenerated: each recorded question carries, at the
    time it was asked, a short statement of what it was probing.
    """

    question: str
    probing_focus: str
    prompt_version: str


class IntentResult(_Strict):
    """ASSUMED shape (A-INTENT-RESULT).

    ``matched_phrase`` comes from UC-05's configured vocabulary, never from the
    learner's free text, so it is safe to log.
    """

    kind: IntentKind
    matched_phrase: str | None = None
    rule: str = "unspecified"


# --------------------------------------------------------------------------
# The dialogue aggregate
# --------------------------------------------------------------------------


class LearnerMessage(_Strict):
    """One message from the learner, retained for the improvement pipeline."""

    text: str
    intent: IntentKind
    received_at: datetime


#: Intents that constitute the learner *answering* the guiding question, as
#: opposed to negotiating with the system about the dialogue itself.  Used only
#: to pick which message the reasoning chain quotes.
_ANSWERING_INTENTS = (
    IntentKind.SUBSTANTIVE_RESPONSE,
    IntentKind.CASUAL_DIFFICULTY,
    IntentKind.LEARNER_REASONED_CONCLUSION,
    IntentKind.EXPLICIT_FRUSTRATION,
)


class ExchangeRecord(_Strict):
    """ASSUMED shape (A-EXCHANGE-DEF).

    **An exchange is one guiding question from the system plus one response
    from the learner to it.**  It is *opened* when the guiding question is
    emitted and *completed* when the learner replies.  ``exchanges_used``
    counts opened exchanges, so a learner who has been asked five guiding
    questions has used five of five.

    ``learner_messages`` holds *every* message received while this exchange was
    the open one -- including exit requests, declines and off-topic asides.
    Those do not open an exchange (that is what keeps "declining an exit leaves
    the count unaffected" true), but they are still the learner's reasoning
    record and are retained in full.
    """

    exchange_number: int
    guiding_question: str
    probing_focus: str
    question_fingerprint: str
    asked_at: datetime
    learner_messages: list[LearnerMessage] = Field(default_factory=list)

    @property
    def learner_response(self) -> str | None:
        """The message that answered this guiding question, if one did."""
        for message in reversed(self.learner_messages):
            if message.intent in _ANSWERING_INTENTS:
                return message.text
        return self.learner_messages[-1].text if self.learner_messages else None

    @property
    def responded_at(self) -> datetime | None:
        return self.learner_messages[-1].received_at if self.learner_messages else None


class ReasoningChainStep(_Strict):
    """ASSUMED shape (A-REASONING-CHAIN).

    Assembled from the recorded dialogue only.  Every field is either copied
    verbatim from an ``ExchangeRecord`` or composed from copied fields; nothing
    here is regenerated, so the chain always reflects what was actually asked.
    """

    exchange_number: int
    guiding_question: str
    probing: str
    learner_response: str | None
    connection_to_answer: str


class Dialogue(_Strict):
    """The persisted, inspectable state machine instance -- one per question.

    Dialogue state is never delegated to a generator's memory.  Everything a
    transition depends on is a field here.
    """

    dialogue_id: str
    session_id: str
    user_id: str
    question_text: str
    topic_tag: str
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    explanation_profile: ExplanationProfile
    practice_area: str | None
    source_status: dict[str, SourceStatus] = Field(default_factory=dict)
    state: DialogueState
    resolution: Resolution | None = None
    exchange_cap: int
    exchanges: list[ExchangeRecord] = Field(default_factory=list)
    prompt_version: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    last_interaction_id: str | None = None
    loop_matched_exchange: int | None = None

    # -- derived ---------------------------------------------------------

    @property
    def exchanges_used(self) -> int:
        return len(self.exchanges)

    @property
    def exchanges_remaining(self) -> int:
        return max(0, self.exchange_cap - self.exchanges_used)

    @property
    def is_open(self) -> bool:
        return self.state in (
            DialogueState.AWAITING_LEARNER_RESPONSE,
            DialogueState.AWAITING_EXIT_CONFIRMATION,
        )

    @property
    def current_question(self) -> ExchangeRecord | None:
        return self.exchanges[-1] if self.exchanges else None

    def previous_questions(self) -> list[str]:
        return [exchange.guiding_question for exchange in self.exchanges]


# --------------------------------------------------------------------------
# Interaction log
# --------------------------------------------------------------------------


class InteractionLogRecord(_Strict):
    """SPECIFIED shape (section 3).  Treat as a published contract.

    ``mode`` is the closed ``Mode`` enum (brief §4.2), and UC-05 always writes
    ``socratic``: it writes records only for responses it produced under
    Socratic mode, and nothing tells it the underlying session type. See the
    known limitation on ``Mode``.  ``rating_state`` is set to ``pending`` and
    never changed by UC-05.
    """

    interaction_id: str
    session_id: str
    user_id: str
    asked_at: datetime
    question_text: str
    topic_tag: str
    naric_level: NaricLevel
    response_id: str
    mode: Mode = Mode.SOCRATIC
    dialogue_id: str
    exchange_number: int
    response_kind: ResponseKind
    resolution: Resolution | None = None
    follow_up_of: str | None = None
    rating_state: RatingState = RatingState.PENDING


# --------------------------------------------------------------------------
# Mode state
# --------------------------------------------------------------------------


class ModeState(_Strict):
    """ASSUMED shape (A-MODE-STATE).

    UC-05 does not own the session record.  This is the *minimum* it persists
    behind ``SessionModeRepository`` so that the setting survives a page
    refresh; an integration engineer repoints the repository at the company's
    session store and this shape becomes a projection of it.

    ``owner_user_id`` is defence in depth: UC-05 cannot ask a session store it
    has not been given, so it records the first learner to set a mode on a
    session and refuses another user thereafter.
    """

    session_id: str
    enabled: bool
    source: ModeSource
    owner_user_id: str | None = None
    updated_at: datetime | None = None

    @classmethod
    def default_for(cls, session_id: str) -> "ModeState":
        """A-MODE-DEFAULT: Socratic mode is off until a learner turns it on."""
        return cls(
            session_id=session_id,
            enabled=False,
            source=ModeSource.DEFAULT,
            owner_user_id=None,
            updated_at=None,
        )
