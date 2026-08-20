"""Platform-internal endpoints (§5, §8, §11, §13, §17).

::

    POST …/system/formal-attempts/{f}/disconnect        the session monitor reports a disconnect
    (§5)
    POST …/system/formal-attempts/{f}/result-resolution  scoring finished; resolve the formal result
    (§8)
    GET  …/system/attempts/{a}/certificate-eligibility   may a certificate be generated? (§11)
    GET  …/system/review-queue/unpublished               reviews the queue has not accepted (§13)
    POST …/system/review-queue/{r}/retry                 publish one again (§13)
    POST …/system/review-queue/retry                     publish all of them (§13)

These are for the platform's own components, not for a browser: the session monitor that notices a
heartbeat has stopped, the scoring pipeline that has finished, the certificate service that is about
to generate, and the job runner or operator that works through a queue backlog. They are guarded by
the `SYSTEM_API_TOKEN` seam rather than by a learner or assessor identity, because none of these
callers is a person.

THE CERTIFICATE CHECK IS THE IMPORTANT ONE
------------------------------------------
``GET …/system/attempts/{a}/certificate-eligibility`` is how the rule in §11 reaches callers UC-09
knows nothing about. A certificate service asks before it generates, and gets `certificate_allowed:
false` with a reason for anything that is not an approved formal pass. An attempt that is not a
formal assessment answers ``NOT_FORMAL_ASSESSMENT``, and the existing UC-05 rules apply unchanged —
UC-09 adds a condition rather than taking over certificates.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.modules.formal_assessment.api.dependencies import ContainerDep, FormalSystemActor
from app.modules.formal_assessment.schemas.requests import DisconnectNotificationRequest
from app.modules.formal_assessment.schemas.responses import (
    CertificateEligibilityResponse,
    FormalReviewModel,
    FormalSubmissionResponse,
    RecoveryReportResponse,
    ResolutionResponse,
    UnpublishedReviewsResponse,
)

router = APIRouter(tags=["Formal assessment — platform"])

FormalAttemptPath = Annotated[str, Path(description="The formal attempt.")]
AttemptPath = Annotated[str, Path(description="The quiz attempt (UC-03).")]
ReviewPath = Annotated[str, Path(description="The formal review.")]


@router.post(
    "/formal-attempts/{formal_attempt_id}/disconnect",
    response_model=FormalSubmissionResponse,
    summary="Report a disconnected formal session",
    description=(
        "For the platform's session monitor: the authoritative session has stopped sending "
        "heartbeats. UC-09 identifies the attempt, takes UC-03's latest valid autosaved state, "
        "submits it with reason `DISCONNECT_AUTO_SUBMIT`, records why, ends the session and "
        "prevents any resume.\n\n"
        "Idempotent — several monitors, or several attempts by one monitor, produce exactly one "
        "submission."
    ),
)
async def report_disconnect(
    formal_attempt_id: FormalAttemptPath,
    actor: FormalSystemActor,
    container: ContainerDep,
    payload: DisconnectNotificationRequest | None = None,
) -> FormalSubmissionResponse:
    body = payload or DisconnectNotificationRequest()
    outcome = await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=formal_attempt_id,
        reported_by=f"SYSTEM:{actor}",
        last_seen_at=body.last_seen_at,
        reason=body.reason or "HEARTBEAT_TIMEOUT",
    )
    return FormalSubmissionResponse.model_validate(outcome.as_dict())


@router.post(
    "/formal-attempts/{formal_attempt_id}/result-resolution",
    response_model=ResolutionResponse,
    summary="Resolve the formal result once scoring has finished",
    description=(
        "Reads UC-04's confirmed score and UC-05's pass/fail decision and records them on the "
        "formal attempt. A pass becomes `PENDING_REVIEW` — **not** a certificate.\n\n"
        "Idempotent and safe to call early: an unconfirmed score or an undetermined result defers, "
        "and an already-resolved attempt reports `ALREADY_RESOLVED`. UC-09 does not score anything "
        "itself."
    ),
)
async def resolve_result(
    formal_attempt_id: FormalAttemptPath,
    actor: FormalSystemActor,
    container: ContainerDep,
) -> ResolutionResponse:
    resolution = await container.services.results.resolve_by_id(formal_attempt_id)
    return ResolutionResponse.model_validate(resolution.as_dict())


@router.get(
    "/attempts/{attempt_id}/certificate-eligibility",
    response_model=CertificateEligibilityResponse,
    summary="May a certificate be generated for this attempt?",
    description=(
        "**The certificate gate (§11).** A certificate service calls this before generating "
        "anything.\n\n"
        "For a formal assessment the answer is `certificate_allowed: true` only when the result "
        "passed *and* an authorised assessor approved it; every other state answers false with a "
        "reason. For an ordinary attempt the answer is `NOT_FORMAL_ASSESSMENT`, meaning UC-09 "
        "imposes no additional condition and the existing rules apply.\n\n"
        "Blocked answers are audited as `CERTIFICATE_BLOCKED`."
    ),
)
async def certificate_eligibility(
    attempt_id: AttemptPath,
    actor: FormalSystemActor,
    container: ContainerDep,
) -> CertificateEligibilityResponse:
    eligibility = await container.services.certificates.check_eligibility_for_attempt(attempt_id)
    return CertificateEligibilityResponse.model_validate(eligibility.as_dict())


@router.get(
    "/review-queue/unpublished",
    response_model=UnpublishedReviewsResponse,
    summary="Pending reviews the assessor queue has not accepted",
    description=(
        "The recovery surface for §13. A review appears here while its publish state is `PENDING` "
        "or `FAILED` — it is fully reviewable through the assessor endpoints regardless, and the "
        "certificate stays blocked. Nothing in this list is lost; it is a work list."
    ),
)
async def list_unpublished(
    actor: FormalSystemActor,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum rows.")] = 100,
) -> UnpublishedReviewsResponse:
    reviews = await container.services.recovery.list_recoverable(limit=limit)
    return UnpublishedReviewsResponse(
        reviews=[FormalReviewModel.model_validate(review.as_dict()) for review in reviews],
        count=len(reviews),
    )


@router.post(
    "/review-queue/{review_id}/retry",
    response_model=FormalReviewModel,
    summary="Publish one pending review to the assessor queue again",
    description=(
        "Emits `QUEUE_RETRY` and republishes. Returns `503 REVIEW_QUEUE_UNAVAILABLE` when the "
        "queue is still down — the caller asked to publish and deserves to know it did not happen. "
        "The review remains recoverable either way, and a retry can never change a decision or "
        "unblock a certificate."
    ),
)
async def retry_publish(
    review_id: ReviewPath,
    actor: FormalSystemActor,
    container: ContainerDep,
) -> FormalReviewModel:
    review = await container.services.recovery.retry(review_id)
    return FormalReviewModel.model_validate(review.as_dict())


@router.post(
    "/review-queue/retry",
    response_model=RecoveryReportResponse,
    summary="Publish every unpublished review again",
    description=(
        "The sweep, for a scheduled job or an operator. Works through the batch without stopping "
        "at the first failure and reports what was published and what is still pending. Safe to "
        "run repeatedly: the queue entry key means a redundant publish collapses rather than "
        "duplicating."
    ),
)
async def retry_all(
    actor: FormalSystemActor,
    container: ContainerDep,
) -> RecoveryReportResponse:
    report = await container.services.recovery.sweep()
    return RecoveryReportResponse.model_validate(report.as_dict())
