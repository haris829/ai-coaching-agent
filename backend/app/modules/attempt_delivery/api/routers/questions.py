"""Question delivery endpoints, supporting both delivery modes.

* **ALL_AT_ONCE** — ``GET /questions`` returns the whole paper.
* **ONE_AT_A_TIME** — ``GET /questions`` is refused with 409
  ``QUESTION_PRESENTATION_VIOLATION``; the client walks the paper with ``/questions/current``,
  ``/questions/at/{position}`` and the persisted cursor.

The mode comes from the attempt's locked configuration, so it cannot change
mid-attempt, and it is enforced here rather than left to the client.

Route order matters: ``/questions/current`` and ``/questions/at/{position}`` are
declared before ``/questions/{question_id}``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.modules.attempt_delivery.api.deps import Context, LearnerId
from app.modules.attempt_delivery.api.presenters import navigation_links, present_question
from app.modules.attempt_delivery.container import RequestContext
from app.modules.attempt_delivery.models import AttemptQuestion, QuizAttempt

router = APIRouter(tags=["Quiz Attempt — Questions"])


def _question_response(
    ctx: RequestContext, attempt: QuizAttempt, question: AttemptQuestion
) -> dict[str, Any]:
    entry = next(
        (
            item
            for item in ctx.submissions.outline(attempt.id)
            if item.question_id == question.question_id
        ),
        None,
    )
    return {
        "attemptId": attempt.id,
        "questionPresentation": attempt.question_presentation,
        "question": present_question(question),
        # The learner's state for this question, so a client rendering one question at
        # a time does not need a second round trip.
        "answerState": {
            "answered": entry.answered if entry else False,
            "complete": entry.complete if entry else False,
            "flagged": entry.flagged if entry else False,
        },
        "navigation": navigation_links(attempt, question),
        "timing": ctx.timing.compute(attempt).to_dict(),
    }


@router.get(
    "/attempts/{attempt_id}/questions",
    summary="Get the whole question set (all-at-once delivery)",
    description=(
        "Returns every delivered question with its current answered/flagged state. "
        "Valid only when the locked configuration's delivery mode is ALL_AT_ONCE; a "
        "one-at-a-time attempt is refused with 409 QUESTION_PRESENTATION_VIOLATION. Correct "
        "answers are never included."
    ),
)
def get_questions(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    attempt, questions = ctx.attempts.get_all_questions(attempt_id, learner_id)
    outline = {entry.question_id: entry for entry in ctx.submissions.outline(attempt.id)}

    return {
        "attemptId": attempt.id,
        "questionPresentation": attempt.question_presentation,
        "totalQuestions": len(questions),
        "timing": ctx.timing.compute(attempt).to_dict(),
        "questions": [
            {
                **present_question(question),
                "answered": (
                    outline[question.question_id].answered
                    if question.question_id in outline
                    else False
                ),
                "complete": (
                    outline[question.question_id].complete
                    if question.question_id in outline
                    else False
                ),
                "flagged": (
                    outline[question.question_id].flagged
                    if question.question_id in outline
                    else False
                ),
            }
            for question in questions
        ],
    }


@router.get(
    "/attempts/{attempt_id}/questions/current",
    summary="Get the question at the persisted cursor (one-at-a-time delivery)",
    description=(
        "The primary read for one-at-a-time delivery, and what a reconnecting learner "
        "resumes on. The cursor lives on the attempt row, so it survives a refresh."
    ),
)
def get_current_question(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    attempt, question = ctx.attempts.get_current_question(attempt_id, learner_id)
    return _question_response(ctx, attempt, question)


@router.get(
    "/attempts/{attempt_id}/questions/at/{position}",
    summary="Get a question by 1-based position",
    description="Positional access for navigating back and forth. Valid in both delivery modes.",
)
def get_question_at_position(
    attempt_id: str, position: int, learner_id: LearnerId, ctx: Context
) -> dict[str, Any]:
    attempt, question = ctx.attempts.get_question_at_position(attempt_id, learner_id, position)
    return _question_response(ctx, attempt, question)


@router.get(
    "/attempts/{attempt_id}/questions/{question_id}",
    summary="Get a delivered question by question id",
    description="Valid in both delivery modes. Correct answers are never included.",
)
def get_question(
    attempt_id: str, question_id: str, learner_id: LearnerId, ctx: Context
) -> dict[str, Any]:
    attempt, question = ctx.attempts.get_question(attempt_id, learner_id, question_id)
    return _question_response(ctx, attempt, question)
