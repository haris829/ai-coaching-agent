"""Transport-agnostic results.

The service returns these; the API layer projects them onto response schemas.
Keeping them separate means the API can add or rename a field without the
service knowing, and the service tests do not go through HTTP.

Nothing here carries a prompt, a provider name, an upstream payload or an
internal exception message.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import (
    DialogueState,
    ExplanationProfile,
    ModeSource,
    NaricLevel,
    NaricLevelSource,
    Resolution,
    ResponseKind,
    SourceStatus,
)
from ..domain.models import FourPartAnswer, ReasoningChainStep


class ContextSummary(BaseModel):
    """What the frontend needs in order to render, and nothing more."""

    model_config = ConfigDict(extra="forbid")

    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    explanation_profile: ExplanationProfile
    practice_area: str | None
    source_status: dict[str, SourceStatus] = Field(default_factory=dict)


class ModeStateResult(BaseModel):
    """The state a frontend needs to render a mode indicator.

    UC-05 exposes this and stops there: no toolbar, no toggle, no indicator.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    enabled: bool
    source: ModeSource
    updated_at: str | None = None
    #: Dialogues closed as a side effect of toggling the mode off.  Present so
    #: a caller can tell the learner what happened to the dialogue they were in.
    closed_dialogue_ids: list[str] = Field(default_factory=list)


class SocraticTurn(BaseModel):
    """One system response, whatever kind it is."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    dialogue_id: str | None
    mode_enabled: bool
    mode_source: ModeSource

    response_kind: ResponseKind
    state: DialogueState | None
    resolution: Resolution | None

    exchanges_used: int
    exchanges_remaining: int
    exchange_cap: int

    acknowledgement: str | None = None
    guiding_question: str | None = None
    exit_offer: str | None = None
    re_entry_offer: str | None = None
    answer: FourPartAnswer | None = None
    reasoning_chain: list[ReasoningChainStep] | None = None

    interaction_id: str | None = None
    #: The name of the state-machine transition that produced this turn.  The
    #: brief requires the machine to be inspectable; this is how a caller (and
    #: a test) sees which rule fired, without reading the log.
    transition: str | None = None

    context: ContextSummary
