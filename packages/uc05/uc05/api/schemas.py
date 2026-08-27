"""API schemas.

Every request model sets ``extra="forbid"``.  That is a security control, not
tidiness: it is what turns an attempt to send ``naric_level``,
``response_kind``, ``resolution``, ``exchanges_used`` or ``system_prompt`` into
a visible 422 rather than a field that is quietly ignored while the caller
believes it took effect.

Note what is *absent* from every request model: ``user_id``.  Identity is
resolved server-side from the transport, never read from a body.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..application.results import ContextSummary, ModeStateResult, SocraticTurn
from ..domain.enums import (
    DialogueState,
    ModeSource,
    Resolution,
    ResponseKind,
)
from ..domain.models import FourPartAnswer, ReasoningChainStep


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


class SetModeRequest(_Request):
    enabled: bool


class StartQuestionRequest(_Request):
    session_id: str = Field(min_length=1, max_length=200)
    question_text: str = Field(min_length=1, max_length=8000)
    #: Optional. When the company supplies its taxonomy this becomes the
    #: canonical value; until then UC-05 derives one deterministically.
    topic_tag: str | None = Field(default=None, max_length=100)


class ReplyRequest(_Request):
    message: str = Field(min_length=1, max_length=8000)


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


class ModeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    enabled: bool
    source: ModeSource
    updated_at: str | None = None
    closed_dialogue_ids: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, result: ModeStateResult) -> "ModeResponse":
        return cls(**result.model_dump())


class ExchangeProgress(BaseModel):
    """Exchanges used and remaining, exposed so a caller can display progress."""

    model_config = ConfigDict(extra="forbid")

    used: int
    remaining: int
    cap: int


class SocraticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    dialogue_id: str | None
    mode_enabled: bool
    mode_source: ModeSource

    response_kind: ResponseKind
    state: DialogueState | None
    resolution: Resolution | None
    exchanges: ExchangeProgress

    acknowledgement: str | None = None
    guiding_question: str | None = None
    exit_offer: str | None = None
    re_entry_offer: str | None = None
    answer: FourPartAnswer | None = None
    reasoning_chain: list[ReasoningChainStep] | None = None

    interaction_id: str | None = None
    transition: str | None = None
    context: ContextSummary

    @classmethod
    def of(cls, turn: SocraticTurn) -> "SocraticResponse":
        data = turn.model_dump()
        data["exchanges"] = {
            "used": data.pop("exchanges_used"),
            "remaining": data.pop("exchanges_remaining"),
            "cap": data.pop("exchange_cap"),
        }
        return cls(**data)


class DialogueMessageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    intent: str
    received_at: str


class DialogueExchangeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange_number: int
    guiding_question: str
    probing_focus: str
    asked_at: str
    learner_messages: list[DialogueMessageView]


class DialogueView(BaseModel):
    """The owner's own dialogue.

    Returned only to the owner, and only ever containing UC-05's own record:
    no prompt, no provider, no generator configuration.
    """

    model_config = ConfigDict(extra="forbid")

    dialogue_id: str
    session_id: str
    question_text: str
    topic_tag: str
    state: DialogueState
    resolution: Resolution | None
    exchanges: ExchangeProgress
    exchange_records: list[DialogueExchangeView]
    context: ContextSummary
    created_at: str
    closed_at: str | None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    exchange_cap: int
