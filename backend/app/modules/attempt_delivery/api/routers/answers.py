"""Answer persistence and autosave endpoints.

Three shapes, all backed by the same idempotent upsert:

* ``PUT .../questions/{question_id}/answer`` — save one answer.
* ``DELETE .../questions/{question_id}/answer`` — clear one answer.
* ``POST .../answers`` — save many at once. This is what a 30-second autosave loop
  calls: one round trip, validated and persisted atomically.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.modules.attempt_delivery.api.deps import Context, LearnerId
from app.modules.attempt_delivery.api.schemas import BatchAnswerRequest, SaveAnswerRequest
from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.domain.enums import AnswerSource
from app.modules.attempt_delivery.services.answer_service import SaveAnswerInput

router = APIRouter(tags=["Quiz Attempt — Answers"])


@router.put(
    "/attempts/{attempt_id}/questions/{question_id}/answer",
    summary="Save one answer",
    description=(
        "Validates the payload against the delivered question snapshot and persists it. "
        "**Idempotent:** re-sending the same response succeeds and reports "
        "`changed: false` without advancing the revision, which is what makes a "
        "periodic autosave safe. Pass `expectedRevision` to detect that another tab or "
        "device moved the answer on. Send `response: null` to clear. Rejected with 409 "
        "once the attempt is submitted, and with 409 ATTEMPT_EXPIRED if the time limit "
        "has elapsed."
    ),
)
def save_answer(
    attempt_id: str,
    question_id: str,
    payload: SaveAnswerRequest,
    learner_id: LearnerId,
    ctx: Context,
) -> dict[str, Any]:
    return ctx.answers.save(
        attempt_id,
        learner_id,
        SaveAnswerInput(
            question_id=question_id,
            response=payload.response,
            source=AnswerSource(payload.source),
            expected_revision=payload.expected_revision,
        ),
    )


@router.delete(
    "/attempts/{attempt_id}/questions/{question_id}/answer",
    summary="Clear one answer",
    description="Returns the question to 'unanswered'. Rejected once the attempt is locked.",
)
def clear_answer(
    attempt_id: str,
    question_id: str,
    learner_id: LearnerId,
    ctx: Context,
) -> dict[str, Any]:
    return ctx.answers.save(
        attempt_id,
        learner_id,
        SaveAnswerInput(question_id=question_id, response=None, source=AnswerSource.MANUAL),
    )


@router.post(
    "/attempts/{attempt_id}/answers",
    summary="Batch save answers (the autosave endpoint)",
    description=(
        "The backend half of the 30-second autosave requirement. The server runs no "
        "timer of its own — the client calls this on whatever cadence it likes.\n\n"
        "**All-or-nothing:** every entry is validated before anything is written, so a "
        "single malformed answer returns 422 and leaves stored state untouched. The "
        "response carries `persistedAt` and authoritative `timing`, letting a client "
        "confirm the save landed and resync its countdown in the same round trip. A "
        "failure response is always structured, so a client can show a persistent "
        "save-failed warning and offer manual retry."
    ),
)
def save_answers(
    attempt_id: str,
    payload: BatchAnswerRequest,
    learner_id: LearnerId,
    ctx: Context,
) -> dict[str, Any]:
    limit = ctx.settings.max_batch_answers
    if len(payload.answers) > limit:
        raise errors.validation_error(
            f'"answers" may contain at most {limit} entries per request.',
            received=len(payload.answers),
            limit=limit,
        )

    entries = [
        SaveAnswerInput(
            question_id=entry.question_id,
            response=entry.response,
            source=AnswerSource(entry.source or payload.source),
            expected_revision=entry.expected_revision,
        )
        for entry in payload.answers
    ]
    return ctx.answers.save_many(attempt_id, learner_id, entries).to_dict()


@router.get(
    "/attempts/{attempt_id}/answers",
    summary="Get the latest persisted answers",
    description=(
        "The reload path. After a refresh or reconnection a client discards its own "
        "state and rebuilds from here. Every delivered question is listed — answered or "
        "not — so 'answered' and 'unanswered' are both explicit."
    ),
)
def list_answers(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    return ctx.answers.list_answers(attempt_id, learner_id)


@router.get(
    "/attempts/{attempt_id}/answers/revisions",
    summary="Get the answer save audit trail",
    description=(
        "Append-only record of every accepted save. Operationally useful for confirming "
        "that an autosave landed and for reconstructing a learner's progress."
    ),
)
def list_answer_revisions(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    revisions = ctx.answers.list_revisions(attempt_id, learner_id)
    return {"attemptId": attempt_id, "revisions": revisions, "count": len(revisions)}
