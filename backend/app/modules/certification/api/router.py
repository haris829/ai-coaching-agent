"""UC-05's HTTP surface. Mounted under UC-03's ``/api/v1`` prefix, because pass/fail is part of the
same learner-facing conversation about one attempt: * ``GET  /attempts/{id}/outcome``
-- pass/fail, certificate state, CPD state * ``POST /attempts/{id}/outcome``
-- determine it (idempotent), or push pending work * ``POST
/attempts/{id}/outcome/certificate/retry``  -- drive a pending certificate, reporting failure *
``POST /attempts/{id}/outcome/cpd/retry``          -- drive a pending CPD synchronisation * ``GET
/outcomes``                                 -- the learner's outcomes, newest attempt first The
two retry endpoints exist because ``POST /outcome`` deliberately *swallows* downstream failures:
determining pass/fail must succeed even when the certificate service is down. When a caller wants
to know whether the retry itself worked, it asks one of these, and a failure is reported as a
retryable 502 rather than hidden."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.composition import ResultsCtx
from app.modules.certification.api.presenters import present_outcome, present_view
from app.modules.identity.security import LearnerIdentity

router = APIRouter(tags=["Quiz Result — Pass/Fail & Certificate (UC-05)"])


@router.get(
    "/attempts/{attempt_id}/outcome",
    summary="Pass/fail, certificate and CPD state",
    description=(
        "The stored outcome for one attempt: `PASS` or `FAIL`, the percentage it was judged on, "
        "and "
        "the pass mark of **the attempt's own configuration version** — reconfiguring the quiz "
        "later "
        "cannot move it.\n\n"
        "`certificate` is present only for a pass, and carries its own status: `PENDING` while the "
        "certificate service has not confirmed issue, `ISSUED` with a certificate number once it "
        "has, `FAILED` when the service rejected it. `attemptsRemaining` is recomputed live from "
        "UC-03's attempt count, which is what a learner who failed needs to see.\n\n"
        "404 when pass/fail has not been determined for this attempt yet."
    ),
)
def get_outcome(attempt_id: str, learner_id: LearnerIdentity, ctx: ResultsCtx) -> dict[str, Any]:
    return present_view(ctx.certification.find_outcome(attempt_id, learner_id=learner_id))


@router.post(
    "/attempts/{attempt_id}/outcome",
    summary="Determine pass/fail (idempotent)",
    description=(
        "Determines pass/fail from the attempt's **confirmed** score, records it, and then "
        "requests a "
        "certificate (on a pass) and a CPD synchronisation.\n\n"
        "**Idempotent.** An attempt that already has an outcome keeps it — the verdict is never "
        "recomputed, because the score behind it is immutable — but any certificate or CPD record "
        "still pending is driven again. That makes this both the first-run path and the recovery "
        "path.\n\n"
        "409 (retryable) when the attempt has no confirmed score yet: pass/fail cannot be "
        "determined "
        "from a pending score. A certificate or CPD failure does **not** fail this call; check "
        "`certificate.status` and `cpd.status` in the response, and use the retry endpoints."
    ),
)
def determine_outcome(
    attempt_id: str, learner_id: LearnerIdentity, ctx: ResultsCtx
) -> dict[str, Any]:
    view = ctx.certification.determine(attempt_id, learner_id=learner_id)
    return {**present_view(view), "created": view.created}


@router.post(
    "/attempts/{attempt_id}/outcome/certificate/retry",
    summary="Retry a pending certificate",
    description=(
        "Re-drives certificate generation for a passing attempt, reusing the existing request so a "
        "retry can never mint a second document. A learner who already holds a certificate for "
        "this "
        "quiz gets that one reported back rather than a duplicate.\n\n"
        "Unlike `POST /outcome`, a failure here is reported: 502 with `retryable: true` when the "
        "certificate service is unavailable. The quiz result and the pass/fail outcome are "
        "unchanged "
        "either way."
    ),
)
def retry_certificate(
    attempt_id: str, learner_id: LearnerIdentity, ctx: ResultsCtx
) -> dict[str, Any]:
    return present_view(ctx.certification.retry_certificate(attempt_id, learner_id=learner_id))


@router.post(
    "/attempts/{attempt_id}/outcome/cpd/retry",
    summary="Retry a pending CPD synchronisation",
    description=(
        "Re-drives the CPD record for this attempt — attempt date, score, pass/fail and course "
        "name "
        "— reusing the existing row so the learner's CPD is never double-logged.\n\n"
        "502 with `retryable: true` when the CPD system is unavailable. A CPD failure never "
        "changes "
        "the quiz result."
    ),
)
def retry_cpd(attempt_id: str, learner_id: LearnerIdentity, ctx: ResultsCtx) -> dict[str, Any]:
    return present_view(ctx.certification.retry_cpd(attempt_id, learner_id=learner_id))


@router.get(
    "/outcomes",
    summary="The learner's pass/fail outcomes",
    description=(
        "Every outcome recorded for this learner, newest attempt first, optionally filtered to one "
        "quiz."
    ),
)
def list_outcomes(
    learner_id: LearnerIdentity,
    ctx: ResultsCtx,
    quiz_id: Annotated[str | None, Query(alias="quizId")] = None,
) -> dict[str, Any]:
    outcomes = ctx.certification.list_outcomes(learner_id, quiz_id=quiz_id)
    return {"outcomes": [present_outcome(outcome) for outcome in outcomes], "total": len(outcomes)}
