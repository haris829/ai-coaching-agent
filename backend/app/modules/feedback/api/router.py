"""UC-06's HTTP surface. Mounted under UC-03's ``/api/v1`` prefix, completing the conversation about
one attempt: * ``GET  /attempts/{id}/feedback``  -- the frozen report * ``POST
/attempts/{id}/feedback``  -- generate it, or return the one already generated * ``GET
/feedback``               -- the learner's reports, newest attempt first The POST is idempotent
and is also the retry path: a report left ``PENDING`` by a failed generation is rebuilt, and one
already ``GENERATED`` is returned untouched. It never re-renders a generated report, because a
historical report that could change is not a historical report."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.composition import ResultsCtx
from app.modules.feedback.api.presenters import present_report, present_summary
from app.modules.identity.security import LearnerIdentity

router = APIRouter(tags=["Quiz Result — Detailed Feedback (UC-06)"])


@router.get(
    "/attempts/{attempt_id}/feedback",
    summary="The detailed feedback report",
    description=(
        "The stored report for one attempt. Per question: the question, the learner's answer, the "
        "correct answer, an explanation, the marks scored and a lesson reference — plus, for a "
        "multi-select, every option's correct/incorrect status and the marks it contributed.\n\n"
        "A missing explanation or lesson reference is reported with a defined fallback string. "
        "Nothing is generated to fill a gap.\n\n"
        "404 when no report has been generated for this attempt, or it is not this learner's."
    ),
)
def get_feedback(attempt_id: str, learner_id: LearnerIdentity, ctx: ResultsCtx) -> dict[str, Any]:
    report, items = ctx.feedback.find_report(attempt_id, learner_id=learner_id)
    return present_report(report, items)


@router.post(
    "/attempts/{attempt_id}/feedback",
    summary="Generate the feedback report (idempotent)",
    description=(
        "Builds the report from the attempt's **confirmed** score, its pass/fail outcome and the "
        "question bank's snapshot for the exact question versions delivered, then freezes it.\n\n"
        "**Idempotent.** An already-generated report is returned unchanged with `replayed: true`;"
        " the "
        "database refuses to modify one. A report left pending by an earlier failure is rebuilt, "
        "so "
        "this is the retry path too.\n\n"
        "409 (retryable) when the attempt has no confirmed score yet. 502 (retryable) when "
        "assembly "
        "failed — the score and the pass/fail outcome are unaffected in both cases."
    ),
)
def generate_feedback(
    attempt_id: str, learner_id: LearnerIdentity, ctx: ResultsCtx
) -> dict[str, Any]:
    outcome = ctx.feedback.generate(attempt_id, learner_id=learner_id)
    return {
        **present_report(outcome.report, outcome.items),
        "replayed": outcome.replayed,
        "created": outcome.created,
    }


@router.get(
    "/feedback",
    summary="The learner's feedback reports",
    description=(
        "Every report recorded for this learner, newest attempt first, optionally filtered to one "
        "quiz. Summary rows only — fetch one attempt's report for the per-question detail."
    ),
)
def list_feedback(
    learner_id: LearnerIdentity,
    ctx: ResultsCtx,
    quiz_id: Annotated[str | None, Query(alias="quizId")] = None,
) -> dict[str, Any]:
    reports = ctx.feedback.list_reports(learner_id, quiz_id=quiz_id)
    return {"reports": [present_summary(report) for report in reports], "total": len(reports)}
