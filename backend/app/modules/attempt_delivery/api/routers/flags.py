"""Question flagging endpoints.

``PUT`` sets the state explicitly (idempotent — the same request twice is fine) and
``DELETE`` is a convenience unflag. Both are refused once the attempt is locked,
because a submitted attempt is immutable in every respect.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.modules.attempt_delivery.api.deps import Context, LearnerId
from app.modules.attempt_delivery.api.schemas import SetFlagRequest

router = APIRouter(tags=["Quiz Attempt — Flags"])


@router.put(
    "/attempts/{attempt_id}/questions/{question_id}/flag",
    summary="Flag or unflag a question",
    description=(
        "Sets the flag state explicitly. Idempotent: repeating the same request is "
        "accepted and refreshes `updatedAt`, and re-flagging an already flagged "
        "question preserves the original `flaggedAt`. State is persisted, so it "
        "survives refresh and reconnection."
    ),
)
def set_flag(
    attempt_id: str,
    question_id: str,
    payload: SetFlagRequest,
    learner_id: LearnerId,
    ctx: Context,
) -> dict[str, Any]:
    return {"flag": ctx.flags.set_flag(attempt_id, learner_id, question_id, payload.flagged)}


@router.delete(
    "/attempts/{attempt_id}/questions/{question_id}/flag",
    summary="Unflag a question",
    description="Convenience equivalent of PUT with `flagged: false`.",
)
def unflag(
    attempt_id: str,
    question_id: str,
    learner_id: LearnerId,
    ctx: Context,
) -> dict[str, Any]:
    return {"flag": ctx.flags.set_flag(attempt_id, learner_id, question_id, False)}


@router.get(
    "/attempts/{attempt_id}/flags",
    summary="Get flag state for every question",
    description=(
        "Includes unflagged questions, so a client can rebuild its full navigation view "
        "from a single response."
    ),
)
def list_flags(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    return ctx.flags.list_flags(attempt_id, learner_id)
