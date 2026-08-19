"""Submission endpoints.

The two-step shape is deliberate and enforced server-side:

* ``GET  .../submission/preview`` — read-only. Summarises what *would* be submitted
  (unanswered, still-flagged, remaining time). Calling it never submits, however many
  times it is called.
* ``POST .../submission`` — the commit. Requires ``confirmed: true``, so a submission
  can only ever result from an explicit confirmation.
* ``POST .../submission/retry`` — re-drives a submission left PENDING by a transient
  downstream failure, reusing the same record so no duplicate is created.
* ``GET  .../submission`` — current submission state and history.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header

from app.modules.attempt_delivery.api.deps import Context, LearnerId
from app.modules.attempt_delivery.api.schemas import (
    ConfirmSubmissionRequest,
    RetrySubmissionRequest,
)

router = APIRouter(tags=["Quiz Attempt — Submission"])


@router.get(
    "/attempts/{attempt_id}/submission/preview",
    summary="Prepare a submission (read-only)",
    description=(
        "Submission *preparation*, clearly separated from the confirmed commit. Returns "
        "the unanswered and still-flagged questions, remaining time, and any `blockers` "
        "that would cause the submission to be refused.\n\n"
        "**This endpoint never submits.** It performs no writes at all, so a client may "
        "call it freely to render a confirmation dialog."
    ),
)
def preview_submission(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    # Loaded permissively: a preview is useful even for an attempt that is already
    # locked, and it must never itself change state.
    attempt = ctx.access.load(attempt_id, learner_id).attempt
    return {"preview": ctx.submissions.preview(attempt)}


@router.post(
    "/attempts/{attempt_id}/submission",
    summary="Confirm and submit the attempt",
    description=(
        "Commits the attempt using the latest successfully saved answers, then locks it: "
        "any later answer or flag update is rejected with 409.\n\n"
        "**Idempotent.** The idempotency key makes a double-click or network retry safe — "
        "the same key always resolves to the same submission, and a completed submission "
        "replays its original response with `idempotentReplay: true` rather than creating "
        "a second record. A key may be supplied in the body or the `Idempotency-Key` "
        "header; omitting it falls back to a key derived from the attempt, so even a "
        "naive client is protected. A *different* key against an already-submitted "
        "attempt returns 409 DUPLICATE_SUBMISSION.\n\n"
        "If the downstream hand-off fails transiently the response is 502 "
        "SUBMISSION_FAILED with `retryable: true` and the submission is left PENDING for "
        "`/submission/retry`."
    ),
)
def confirm_submission(
    attempt_id: str,
    payload: ConfirmSubmissionRequest,
    learner_id: LearnerId,
    ctx: Context,
    idempotency_key_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    idempotency_key = (
        payload.idempotency_key
        or (idempotency_key_header.strip() if idempotency_key_header else None)
        or f"attempt-{attempt_id}-submit"
    )

    # A pending attempt is loaded rather than refused here, because whether this request
    # is a legitimate retry depends on the idempotency key — and only the submission
    # service knows that. It re-drives a submission whose key matches, and refuses a
    # *different* key with ATTEMPT_SUBMISSION_PENDING.
    attempt = ctx.access.load_for_submission(attempt_id, learner_id, allow_pending=True)
    result = ctx.submissions.confirm(
        attempt, idempotency_key=idempotency_key, confirmed=payload.confirmed
    )
    # 200 rather than 201: submission mutates the attempt rather than creating a new
    # addressable resource, and a replay must be indistinguishable from the original.
    return {**result.body, "idempotentReplay": result.idempotent_replay}


@router.post(
    "/attempts/{attempt_id}/submission/retry",
    summary="Retry a pending submission",
    description=(
        "Re-drives a submission stuck in PENDING after a transient downstream failure. "
        "Accepts SUBMISSION_PENDING attempts — that is precisely the state this endpoint "
        "exists to clear. Reuses the existing submission record, so retrying never "
        "creates a duplicate. A retry of an already-completed submission replays its "
        "stored response."
    ),
)
def retry_submission(
    attempt_id: str,
    learner_id: LearnerId,
    ctx: Context,
    payload: RetrySubmissionRequest | None = None,
) -> dict[str, Any]:
    attempt = ctx.access.load_for_submission(attempt_id, learner_id, allow_pending=True)
    result = ctx.submissions.retry(
        attempt, idempotency_key=payload.idempotency_key if payload else None
    )
    return {**result.body, "idempotentReplay": result.idempotent_replay}


@router.get(
    "/attempts/{attempt_id}/submission",
    summary="Get submission state and history",
    description=(
        "Lets a client distinguish PENDING / SUBMITTED / FAILED and decide whether to "
        "offer a retry. `history` lists every submission record for the attempt, which "
        "is also how a caller can verify that repeated submits produced no duplicates."
    ),
)
def get_submission(attempt_id: str, learner_id: LearnerId, ctx: Context) -> dict[str, Any]:
    attempt = ctx.access.load(attempt_id, learner_id).attempt
    state = ctx.submissions.describe(attempt.id)
    presented = ctx.timing.compute(attempt)
    return {
        "attemptId": attempt.id,
        "attemptStatus": attempt.status,
        "submittedAt": presented.submitted_at,
        "submissionReason": attempt.submission_reason,
        **state,
    }
