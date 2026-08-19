"""Attempt lifecycle endpoints.

Route order matters: the literal ``/attempts/active`` is declared before the
``/attempts/{attempt_id}`` parameter route so it is not swallowed as an id.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response, status

from app.modules.attempt_delivery.api.deps import Context, LearnerId
from app.modules.attempt_delivery.api.presenters import present_attempt
from app.modules.attempt_delivery.api.schemas import CreateAttemptRequest, SetCursorRequest
from app.modules.attempt_delivery.domain.enums import QuestionPresentation

router = APIRouter(tags=["Quiz Attempt — Attempts"])


@router.get(
    "/quizzes/{quiz_id}/attempt-eligibility",
    summary="Check whether a new attempt may be started",
    description=(
        "Pre-flight check. Reports enrolment, remaining attempts and any blocking "
        "reason **without creating anything**, so a client can show "
        '"2 of 3 attempts remaining" or explain a refusal up front. Applies exactly '
        "the checks that attempt creation applies."
    ),
)
def check_eligibility(quiz_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    report = ctx.attempts.check_eligibility(learner_id, quiz_id)
    return {"eligibility": report.to_dict()}


@router.post(
    "/attempts",
    status_code=status.HTTP_201_CREATED,
    summary="Create a quiz attempt",
    description=(
        "Validates eligibility, **locks the active UC-01 configuration version** onto "
        "the attempt, selects the question set from the UC-02 bank and freezes it. "
        "All validation happens before any write, and the attempt plus its complete "
        "question set are committed in one transaction."
    ),
)
def create_attempt(
    payload: CreateAttemptRequest,
    learner_id: LearnerId,
    ctx: Context,
    response: Response,
) -> dict[str, Any]:
    result = ctx.attempts.create_attempt(learner_id, payload.quiz_id)
    attempt = result.attempt
    response.headers["Location"] = f"/api/v1/attempts/{attempt.id}"

    questions_url = (
        f"/api/v1/attempts/{attempt.id}/questions"
        if attempt.question_presentation == str(QuestionPresentation.ALL_AT_ONCE)
        else f"/api/v1/attempts/{attempt.id}/questions/current"
    )

    return {
        "attempt": present_attempt(attempt, ctx.timing.compute(attempt).to_dict()),
        "delivery": {
            "questionPresentation": attempt.question_presentation,
            "totalQuestions": attempt.total_questions,
            "questionTypeCounts": result.type_counts,
            # Where to fetch questions, given the locked delivery mode.
            "questionsUrl": questions_url,
        },
    }


@router.get(
    "/attempts",
    summary="List the learner's attempts for a quiz",
    description="Attempt history, oldest first. Useful for showing attempts used.",
)
def list_attempts(
    learner_id: LearnerId,
    ctx: Context,
    quiz_id: str = Query(alias="quizId", min_length=1),
) -> dict[str, Any]:
    attempts = ctx.attempts.list_attempts(learner_id, quiz_id)
    return {
        "attempts": [present_attempt(attempt) for attempt in attempts],
        "count": len(attempts),
    }


@router.get(
    "/attempts/active",
    summary="Get the attempt currently in progress",
    description=(
        "The reload / reconnection entry point: returns the attempt in progress so a "
        "client that has lost its state can pick up the authoritative one. If the time "
        "limit has elapsed in the meantime, the attempt is submitted first and its "
        "final state is returned."
    ),
)
def get_active_attempt(
    learner_id: LearnerId,
    ctx: Context,
    quiz_id: str = Query(alias="quizId", min_length=1),
) -> dict[str, Any]:
    attempt = ctx.attempts.get_open_attempt(learner_id, quiz_id)
    return {"attempt": present_attempt(attempt, ctx.timing.compute(attempt).to_dict())}


@router.get(
    "/attempts/{attempt_id}",
    summary="Get an attempt",
    description=(
        "Includes the locked configuration and server-authoritative timing. Visible "
        "only to the owning learner; another learner's attempt is indistinguishable "
        "from one that does not exist."
    ),
)
def get_attempt(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    attempt = ctx.attempts.get_attempt(attempt_id, learner_id)
    return {"attempt": present_attempt(attempt, ctx.timing.compute(attempt).to_dict())}


@router.get(
    "/attempts/{attempt_id}/state",
    summary="Get navigation and completion state",
    description=(
        "Per-question answered / complete / flagged state plus counts and authoritative "
        "timing — everything a client needs to render a question navigator and resume "
        "where the learner left off."
    ),
)
def get_attempt_state(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    return {"state": ctx.attempts.get_navigation_state(attempt_id, learner_id)}


@router.get(
    "/attempts/{attempt_id}/timing",
    summary="Get server-authoritative timing",
    description=(
        "The only source a client should trust for its countdown. `clientTime` may be "
        "supplied purely so the response can report the observed skew; it never "
        "influences the remaining time, so a manipulated device clock cannot extend an "
        "attempt. Resync whenever the reported skew exceeds "
        "`clockResyncThresholdSeconds`."
    ),
)
def get_attempt_timing(
    attempt_id: str,
    learner_id: LearnerId,
    ctx: Context,
    client_time: str | None = Query(
        default=None,
        alias="clientTime",
        description="Optional ISO-8601 instant from the client, echoed back as a skew.",
    ),
) -> dict[str, Any]:
    attempt = ctx.attempts.get_attempt(attempt_id, learner_id)
    return {
        "timing": ctx.timing.compute(attempt, client_time=client_time).to_dict(),
        "attempt": {
            "attemptId": attempt.id,
            "status": attempt.status,
            "submittedAt": present_attempt(attempt)["submittedAt"],
            "submissionReason": attempt.submission_reason,
        },
    }


@router.put(
    "/attempts/{attempt_id}/cursor",
    summary="Persist the learner's current question position",
    description=(
        "Stores the navigation cursor on the attempt so one-at-a-time delivery survives "
        "a refresh. Rejected once the attempt is locked."
    ),
)
def set_cursor(
    attempt_id: str,
    payload: SetCursorRequest,
    learner_id: LearnerId,
    ctx: Context,
) -> dict[str, Any]:
    attempt = ctx.attempts.set_cursor(attempt_id, learner_id, payload.position)
    return {"attempt": present_attempt(attempt, ctx.timing.compute(attempt).to_dict())}
