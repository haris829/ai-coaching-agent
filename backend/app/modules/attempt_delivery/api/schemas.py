"""Pydantic request models.

Only *request* envelopes are modelled here. Answer payloads are validated in the
domain layer instead, because the valid shape depends on the delivered question
snapshot — something no static schema can express. ``response`` is therefore typed
loosely on purpose and handed to
:func:`app.domain.answer_validation.validate_answer`, which produces precise,
field-level errors.

Every model forbids unknown fields so a typo in a client payload is reported rather
than silently ignored, and booleans/integers are *strict*: over a JSON API, ``"yes"``
is not a boolean and ``"3"`` is not an integer, so Pydantic's lax coercion is disabled
rather than allowed to guess at a client's intent.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

_STRICT = ConfigDict(extra="forbid")


class CreateAttemptRequest(BaseModel):
    """Body for ``POST /attempts``."""

    model_config = _STRICT

    quiz_id: str = Field(
        alias="quizId",
        min_length=1,
        max_length=64,
        description="Identifier of the quiz (owned by UC-01) to attempt.",
    )


class SaveAnswerRequest(BaseModel):
    """Body for ``PUT /attempts/{attempt_id}/questions/{question_id}/answer``."""

    model_config = _STRICT

    #: The answer payload. Shape depends on the question type; send ``null`` to clear.
    #: Validated against the delivered question snapshot, not by this schema.
    response: Any = Field(
        default=None,
        description=(
            "Answer payload, validated against the delivered question. "
            "SINGLE_CHOICE: {selectedOptionId}. TRUE_FALSE: {value}. "
            "MULTI_SELECT: {selectedOptionIds}. DRAG_TO_ORDER: {orderedItemIds}. "
            "SCENARIO: {responses:[{subQuestionId, answer}]}. Send null to clear."
        ),
    )
    source: Literal["MANUAL", "AUTOSAVE"] = Field(
        default="MANUAL", description="Whether this save came from a learner action or autosave."
    )
    expected_revision: StrictInt | None = Field(
        default=None,
        alias="expectedRevision",
        ge=0,
        description="Optimistic-concurrency guard: reject if the stored revision differs.",
    )


class BatchAnswerEntry(BaseModel):
    """One entry in a batch autosave."""

    model_config = _STRICT

    question_id: str = Field(alias="questionId", min_length=1, max_length=64)
    response: Any = Field(default=None)
    source: Literal["MANUAL", "AUTOSAVE"] | None = Field(default=None)
    expected_revision: StrictInt | None = Field(default=None, alias="expectedRevision", ge=0)


class BatchAnswerRequest(BaseModel):
    """Body for ``POST /attempts/{attempt_id}/answers`` — the autosave endpoint."""

    model_config = _STRICT

    answers: list[BatchAnswerEntry] = Field(
        min_length=1, description="Answers to persist atomically."
    )
    source: Literal["MANUAL", "AUTOSAVE"] = Field(
        default="AUTOSAVE", description="Default source applied to entries that omit one."
    )


class SetFlagRequest(BaseModel):
    """Body for ``PUT /attempts/{attempt_id}/questions/{question_id}/flag``."""

    model_config = _STRICT

    flagged: StrictBool = Field(
        description="True to flag the question for review, false to unflag."
    )


class SetCursorRequest(BaseModel):
    """Body for ``PUT /attempts/{attempt_id}/cursor``."""

    model_config = _STRICT

    position: StrictInt = Field(ge=1, description="1-based position to resume from.")


class ConfirmSubmissionRequest(BaseModel):
    """Body for ``POST /attempts/{attempt_id}/submission``."""

    model_config = _STRICT

    confirmed: StrictBool = Field(
        description=(
            "Must be true. The preview endpoint cannot submit, so the commit always "
            "carries the learner's explicit confirmation."
        )
    )
    idempotency_key: str | None = Field(
        default=None,
        alias="idempotencyKey",
        min_length=1,
        max_length=200,
        description=(
            "Makes a double-click or network retry safe. Defaults to a key derived "
            "from the attempt when omitted."
        ),
    )


class RetrySubmissionRequest(BaseModel):
    """Body for ``POST /attempts/{attempt_id}/submission/retry``."""

    model_config = _STRICT

    idempotency_key: str | None = Field(
        default=None,
        alias="idempotencyKey",
        min_length=1,
        max_length=200,
        description="Target a specific submission record. Defaults to the pending one.",
    )
