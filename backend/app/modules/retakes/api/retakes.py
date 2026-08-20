"""Learner-facing retake endpoints.

::

    GET  /v1/quizzes/{q}/retake-eligibility   may this learner retake, and why not
    POST /v1/quizzes/{q}/retakes              create a retake (idempotent)
    GET  /v1/quizzes/{q}/retakes              the learner's retakes for this quiz
    GET  /v1/retakes/{r}                      one retake
    GET  /v1/quizzes/{q}/attempt-history      the full attempt history

**The learner is not in the path.** UC-08 shipped with ``/learners/{id}/…`` because it had no
identity layer to consult; here the learner comes from the bearer token through the one
authentication seam, exactly as UC-03's ``/v1/attempts`` and UC-07's coaching endpoints do. The
paths sit under UC-03's ``/v1`` prefix because a retake is a new attempt, and that is where a
learner client already talks about attempts.

The handlers are deliberately thin: resolve identity, call one service, map the result onto a
response model. Every decision — eligibility, the configuration version, the exclusion plan, the
difference check — is made in the services, so the same rules apply to a host application that
calls them directly without going through HTTP.

``POST …/retakes`` returns **201** for a retake that was created and **200** for one that already
existed, which is how a client tells "created" from "your retry found the existing one" without
either being an error (§16).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app.modules.retakes.api.dependencies import RetakeCtx, RetakeLearner
from app.modules.retakes.schemas.requests import CreateRetakeRequest
from app.modules.retakes.schemas.responses import (
    AttemptHistoryResponse,
    DeliveredAttemptModel,
    EligibilityResponse,
    QuestionPlanModel,
    QuestionSetDifferenceModel,
    RetakeListResponse,
    RetakeModel,
    RetakeResponse,
)
from app.modules.retakes.services.retake_service import RetakeOutcome

router = APIRouter(tags=["Retakes"])

QuizPath = Annotated[str, Path(description="The quiz being retaken.")]


@router.get(
    "/quizzes/{quiz_id}/retake-eligibility",
    response_model=EligibilityResponse,
    summary="May this learner retake this quiz, and on what allowance",
    description=(
        "The authoritative eligibility answer. Calculated entirely from backend data — the "
        "configuration version locked to the learner's history, UC-03's attempt count, this "
        "module's in-flight reservations and any administrator grant. A client renders `state` "
        "and `guidance`; it never computes eligibility from an attempt count of its own."
    ),
)
async def get_eligibility(
    quiz_id: QuizPath,
    learner_id: RetakeLearner,
    container: RetakeCtx,
) -> EligibilityResponse:
    eligibility = await container.services.eligibility.check(learner_id, quiz_id)
    return EligibilityResponse.model_validate(eligibility.as_dict())


@router.post(
    "/quizzes/{quiz_id}/retakes",
    response_model=RetakeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a retake attempt",
    description=(
        "Creates a completely independent new attempt. The previous attempt, its answers, its "
        "score, its pass/fail result and its question set are never modified.\n\n"
        "Idempotent without a client token: the key is derived from the learner, the quiz and the "
        "previous attempt, so a retry after a timeout returns the attempt that already exists "
        "with status **200** instead of creating a second one."
    ),
)
async def create_retake(
    quiz_id: QuizPath,
    learner_id: RetakeLearner,
    container: RetakeCtx,
    response: Response,
    payload: CreateRetakeRequest | None = None,
) -> RetakeResponse:
    body = payload or CreateRetakeRequest()
    outcome = await container.services.retakes.create(
        learner_id=learner_id,
        quiz_id=quiz_id,
        previous_attempt_id=body.previous_attempt_id,
    )
    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
    return _as_response(outcome)


@router.get(
    "/quizzes/{quiz_id}/retakes",
    response_model=RetakeListResponse,
    summary="The learner's retake records for one quiz",
)
async def list_retakes(
    quiz_id: QuizPath,
    learner_id: RetakeLearner,
    container: RetakeCtx,
) -> RetakeListResponse:
    retakes = await container.services.retakes.list_for_quiz(learner_id, quiz_id)
    return RetakeListResponse(
        learner_id=learner_id,
        quiz_id=quiz_id,
        retakes=[RetakeModel.model_validate(item.as_dict()) for item in retakes],
    )


@router.get(
    "/retakes/{retake_id}",
    response_model=RetakeModel,
    summary="One retake record",
    description=(
        "Ownership-scoped: a retake belonging to another learner returns 404 rather than "
        "revealing that it exists."
    ),
)
async def get_retake(
    retake_id: Annotated[str, Path(description="The retake record id.")],
    learner_id: RetakeLearner,
    container: RetakeCtx,
) -> RetakeModel:
    retake = await container.services.retakes.get(learner_id, retake_id)
    return RetakeModel.model_validate(retake.as_dict())


@router.get(
    "/quizzes/{quiz_id}/attempt-history",
    response_model=AttemptHistoryResponse,
    summary="Every attempt this learner has made at this quiz",
    description=(
        "Assembled read-only from UC-03 (attempts), UC-04 (scores), UC-05 (pass/fail), UC-06 "
        "(feedback availability), UC-07 (coaching availability) and this module's retake "
        "relationships. Nothing is stored here and nothing is recomputed; a fact an upstream "
        "module has not produced is labelled unavailable rather than filled in."
    ),
)
async def get_attempt_history(
    quiz_id: QuizPath,
    learner_id: RetakeLearner,
    container: RetakeCtx,
) -> AttemptHistoryResponse:
    history = await container.services.history.for_learner_quiz(learner_id, quiz_id)
    return AttemptHistoryResponse.model_validate(history.as_dict())


def _as_response(outcome: RetakeOutcome) -> RetakeResponse:
    """Map a service outcome onto the HTTP contract.

    The plan and the difference appear both on the retake record and at the top level: the record
    is the durable audit trail, and the top-level copy is what a client renders without having to
    know that the record carries a snapshot.
    """
    return RetakeResponse(
        retake=RetakeModel.model_validate(outcome.retake.as_dict()),
        attempt=(
            DeliveredAttemptModel.model_validate(
                {
                    "attempt_id": outcome.attempt.attempt_id,
                    "learner_id": outcome.attempt.learner_id,
                    "course_id": outcome.attempt.course_id,
                    "quiz_id": outcome.attempt.quiz_id,
                    "attempt_number": outcome.attempt.attempt_number,
                    "status": str(outcome.attempt.status),
                    "configuration_version_id": outcome.attempt.configuration_version_id,
                    "configuration_version_number": (
                        outcome.attempt.configuration_version_number
                    ),
                    "delivered_question_ids": list(outcome.attempt.delivered_question_ids),
                    "total_questions": outcome.attempt.total_questions,
                    "started_at": outcome.attempt.started_at,
                    "delivery_mode": outcome.attempt.delivery_mode,
                    "time_limit_seconds": outcome.attempt.time_limit_seconds,
                }
            )
            if outcome.attempt
            else None
        ),
        eligibility=EligibilityResponse.model_validate(outcome.eligibility.as_dict()),
        question_plan=(
            QuestionPlanModel.model_validate(outcome.plan.as_dict()) if outcome.plan else None
        ),
        question_set_difference=(
            QuestionSetDifferenceModel.model_validate(outcome.difference.as_dict())
            if outcome.difference
            else None
        ),
        replayed=outcome.replayed,
    )
