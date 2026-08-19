"""Response models (§31).

These describe the contract the coaching UI integrates against. Three things are worth pointing at
when reading them:

* **They speak camelCase.** Every model extends ``app.core.schemas.CamelModel``, which is what the
  whole API speaks because a TypeScript client consumes it. Validation still accepts the snake_case
  the services produce (``populate_by_name``), so the translation is one declaration rather than a
  mapping layer per endpoint.
* **``coachingAvailable`` appears at every level** — the attempt, each question, each review item,
  each session operation. That is the backend half of "Show Review with Larry" (§4, §10): UC-07
  states whether the action may be offered and the frontend decides how to render it.
* **No response model has a field for a correct answer.** Not on a question, not on a review item,
  not on a coaching turn. The answer key does not reach the model, and it does not reach the client
  through this module either (§12).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.core.schemas import CamelModel


class SessionModel(CamelModel):
    """The coaching session's state (§17)."""

    session_id: str
    learner_id: str
    attempt_id: str
    course_id: str
    question_id: str
    question_position: int | None = None
    topic: str | None = None
    mode: str
    status: str
    exchange_count: int
    #: The five-exchange transition, as a flag a frontend can act on (§15).
    direct_explanation_available: bool
    direct_explanation_offered: bool
    direct_explanation_threshold: int
    exchanges_until_choice: int
    started_at: str
    updated_at: str
    completed_at: str | None = None
    last_failure_code: str | None = None
    revision: int


class MessageModel(CamelModel):
    """One turn of the conversation."""

    role: str
    content: str
    index: int
    created_at: str
    mode: str | None = None


class SanitizationModel(CamelModel):
    """What the answer-key sanitiser removed on the way in (§13, §22).

    Field names and counts only. A value in here would recreate the leak one layer down, in exactly
    the place people forget to look — which is why the model has no field that could hold one.
    """

    removed_fields: list[str] = Field(default_factory=list)
    scrubbed_fields: list[str] = Field(default_factory=list)
    forbidden_value_count: int = 0
    contamination_findings: list[str] = Field(default_factory=list)
    answer_key_excluded: bool = True


class SessionStateModel(CamelModel):
    session: SessionModel
    message_count: int
    messages: list[MessageModel] = Field(default_factory=list)


class StartCoachingModel(CamelModel):
    """The result of starting or resuming coaching (§30)."""

    outcome: str = Field(description="STARTED, RESUMED or UNAVAILABLE.")
    coaching_available: bool
    reason: str | None = Field(
        default=None,
        description="Error code when the coach could not speak. Never a provider message (§29).",
    )
    sanitization: SanitizationModel | None = None
    session: SessionModel
    message_count: int
    messages: list[MessageModel] = Field(default_factory=list)


class ExchangeModel(CamelModel):
    """The result of one coach turn (§27, §28)."""

    outcome: str = Field(description="COMPLETED or UNAVAILABLE.")
    coaching_available: bool
    reason: str | None = None
    retryable: bool = False
    reply: MessageModel | None = None
    session: SessionModel
    message_count: int
    messages: list[MessageModel] = Field(default_factory=list)


class QuestionEligibilityModel(CamelModel):
    question_id: str
    position: int
    outcome: str
    coaching_available: bool
    reason: str


class EligibilityModel(CamelModel):
    """Whether coaching may be offered, and for which questions (§10)."""

    attempt_id: str
    coaching_available: bool
    reason: str
    message: str | None = None
    retryable: bool
    #: The observed upstream state behind a refusal — which precondition is outstanding. Free-form
    #: rather than typed, because it names whichever of UC-03/UC-04/UC-06 said no.
    details: dict[str, Any] | None = None
    questions: list[QuestionEligibilityModel] = Field(default_factory=list)
    incorrect_question_count: int = 0


class ReviewItemModel(CamelModel):
    question_id: str
    position: int
    status: str
    topic: str | None = None
    session_id: str | None = None
    exchange_count: int = 0
    coaching_available: bool


class ReviewQueueModel(CamelModel):
    """Every incorrect question on the attempt, in delivery order (§19)."""

    attempt_id: str
    total_incorrect: int
    completed_count: int
    remaining_count: int
    finished: bool
    items: list[ReviewItemModel] = Field(default_factory=list)
    next_question_id: str | None = None


class ReviewAdvanceModel(CamelModel):
    completed_question_id: str | None = None
    next_question: ReviewItemModel | None = None
    review: ReviewQueueModel
