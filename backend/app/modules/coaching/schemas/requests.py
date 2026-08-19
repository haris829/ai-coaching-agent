"""Request bodies (§31).

Note what a client cannot send. There is no field for a question's correct answer, no field for a
system prompt, and no field that lets a caller choose what goes into the coaching context. The only
things a learner contributes to the model's input are their message and their choice of mode — the
context is assembled server-side from authoritative sources and sanitised (§13, §26).

``extra="forbid"`` is part of that. A body with an unrecognised key is rejected rather than quietly
ignored, so a client cannot probe for a field that might once have existed.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from app.core.schemas import CamelModel
from app.modules.coaching.domain.enums import CoachingMode


class SendMessageRequest(CamelModel):
    """One learner turn in the conversation."""

    model_config = ConfigDict(**{**CamelModel.model_config, "extra": "forbid"})

    message: str = Field(
        min_length=1,
        max_length=100_000,
        description=(
            "The learner's message to the coach. The configured per-message limit is applied by "
            "the service; this bound only stops an unreasonable payload reaching it."
        ),
    )


class SelectModeRequest(CamelModel):
    """The learner's choice at the five-exchange transition (§15, §16)."""

    model_config = ConfigDict(**{**CamelModel.model_config, "extra": "forbid"})

    mode: CoachingMode = Field(
        description=(
            "SOCRATIC to keep working the question through, DIRECT_EXPLANATION to have the "
            "concept explained. DIRECT_EXPLANATION is refused until the exchange threshold has "
            "been reached."
        )
    )


class NextQuestionRequest(CamelModel):
    """Moving through the review queue (§19)."""

    model_config = ConfigDict(**{**CamelModel.model_config, "extra": "forbid"})

    complete_current: bool = Field(
        default=True,
        description=(
            "Finish with the question currently being coached before returning the next one. "
            "Set false to look ahead without leaving the current question."
        ),
    )
