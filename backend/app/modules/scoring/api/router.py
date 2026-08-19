"""UC-04's HTTP surface. Three endpoints, mounted under UC-03's ``/api/v1`` prefix because they are
part of the same learner-facing conversation about one attempt: * ``GET  /attempts/{id}/result``
-- the score, with a per-question breakdown * ``POST /attempts/{id}/result``       -- score the
attempt, or replay the score it already has * ``GET  /results``                    -- the
learner's results, newest attempt first The POST is deliberately idempotent rather than a
"create". Scoring normally happens automatically during submission; this endpoint exists so a
result left ``PENDING_SCORE`` by a transient failure can be driven again, and calling it against
an already-scored attempt returns that score unchanged instead of recomputing it. That is why it
answers ``200`` and never ``201``. Routers are thin: no business decision is made in this file."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.composition import ResultsCtx
from app.modules.identity.security import LearnerIdentity
from app.modules.scoring.api.presenters import present_full, present_result

router = APIRouter(tags=["Quiz Result — Scoring (UC-04)"])


@router.get(
    "/attempts/{attempt_id}/result",
    summary="The attempt's score",
    description=(
        "The stored result for one attempt, with the marks awarded per question.\n\n"
        "`status` is either `SCORED` or `PENDING_SCORE`; `statusLabel` carries the wording a "
        "learner should see, which for a pending result is **Submitted — Pending Score**. A "
        "pending result also carries `anomalies` explaining what blocked confirmation.\n\n"
        "404 when the attempt has never been scored, or is not this learner's."
    ),
)
def get_result(attempt_id: str, learner_id: LearnerIdentity, ctx: ResultsCtx) -> dict[str, Any]:
    result = ctx.scoring.find_result(attempt_id, learner_id=learner_id)
    return present_full(result, ctx.scoring.question_scores(result.id))


@router.post(
    "/attempts/{attempt_id}/result",
    summary="Score the attempt (idempotent)",
    description=(
        "Scores a submitted attempt and stores the result.\n\n"
        "**Idempotent.** An attempt that is already `SCORED` has its stored result replayed with "
        "`replayed: true` and nothing is written — a confirmed score cannot be recomputed, and the "
        "database enforces that as well as this service. An attempt left `PENDING_SCORE` is scored "
        "again, so this is also the retry path.\n\n"
        "409 while the attempt is still in progress; 503 (retryable) if the result could not be "
        "persisted, in which case nothing was saved."
    ),
)
def score_attempt(attempt_id: str, learner_id: LearnerIdentity, ctx: ResultsCtx) -> dict[str, Any]:
    outcome = ctx.scoring.score(attempt_id, learner_id=learner_id)
    return {
        **present_full(outcome.result, outcome.question_scores),
        "replayed": outcome.replayed,
        "created": outcome.created,
    }


@router.get(
    "/results",
    summary="The learner's results",
    description=(
        "Every result recorded for this learner, newest attempt first, optionally filtered to one "
        "quiz. Summary rows only — fetch one attempt's result for the per-question breakdown."
    ),
)
def list_results(
    learner_id: LearnerIdentity,
    ctx: ResultsCtx,
    quiz_id: Annotated[str | None, Query(alias="quizId")] = None,
) -> dict[str, Any]:
    results = ctx.scoring.list_results(learner_id, quiz_id=quiz_id)
    return {"results": [present_result(result) for result in results], "total": len(results)}
