"""Assessor endpoints (§10, §17).

::

    GET  …/assessor/pending-reviews              the queue, scoped to the assessor's courses
    GET  …/assessor/reviews/{r}                   everything needed to decide
    POST …/assessor/reviews/{r}/review-start      record that this assessor opened it
    POST …/assessor/reviews/{r}/decision          APPROVED or REQUIRES_FURTHER_REVIEW
    POST …/assessor/reviews/{r}/certificate-workflow   retry the certificate trigger after an
    approval

**These are API contracts, not a dashboard.** There is no assessor UI here; the company's frontend
consumes these endpoints.

Every route performs both halves of the check §19 requires: the `X-Assessor-Id` header plus the
optional `ASSESSOR_API_TOKEN` bearer establish *who* is calling, and the assessor directory
establishes whether they may review *this course*. The second check happens on every single call, in
the service, so a valid token is never sufficient and an assessor who loses access to a course loses
it mid-session.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.modules.formal_assessment.api.dependencies import ContainerDep, FormalAssessor
from app.modules.formal_assessment.schemas.requests import AssessorDecisionRequest
from app.modules.formal_assessment.schemas.responses import (
    CertificateTriggerResponse,
    DecisionResponse,
    FormalReviewModel,
    PendingReviewsResponse,
    ReviewDetailResponse,
)

router = APIRouter(tags=["Formal assessment — assessor"])

ReviewPath = Annotated[str, Path(description="The formal review.")]


@router.get(
    "/pending-reviews",
    response_model=PendingReviewsResponse,
    summary="Formal assessments awaiting review",
    description=(
        "Passing formal assessments waiting for a human decision, oldest first, scoped to the "
        "courses this assessor is authorised for. An assessor authorised for no courses sees an "
        "empty queue — never the whole table.\n\n"
        "Reviews appear here whether or not the assessor queue accepted them: this list reads the "
        "durable review records, which is why a queue outage delays a notification rather than "
        "losing an assessment."
    ),
)
async def list_pending_reviews(
    assessor: FormalAssessor,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=200, description="Page size.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> PendingReviewsResponse:
    page = await container.services.reviews.list_pending(
        assessor_id=assessor, limit=limit, offset=offset
    )
    return PendingReviewsResponse.model_validate(page.as_dict())


@router.get(
    "/reviews/{review_id}",
    response_model=ReviewDetailResponse,
    summary="Everything needed to review one formal assessment",
    description=(
        "The learner and their identity confirmation, the assessment and the conditions they "
        "acknowledged, the score, every question and response, the attempt, the submission and any "
        "disconnect, the anomaly flags, and the supervision trail including any device that was "
        "turned away.\n\n"
        "The learner's name and email address are included — read live from the profile source for "
        "this authorised assessor — because confirming that the right person sat the assessment "
        "cannot be done from an identifier."
    ),
)
async def get_review(
    review_id: ReviewPath,
    assessor: FormalAssessor,
    container: ContainerDep,
) -> ReviewDetailResponse:
    detail = await container.services.reviews.get_detail(assessor_id=assessor, review_id=review_id)
    return ReviewDetailResponse.model_validate(detail.as_dict())


@router.post(
    "/reviews/{review_id}/review-start",
    response_model=FormalReviewModel,
    summary="Record that this assessor has opened the review",
    description=(
        "Moves the review to `IN_REVIEW` and records who is looking. Not a lock — it stops two "
        "assessors unknowingly duplicating the work, and it is refused once a decision exists."
    ),
)
async def start_review(
    review_id: ReviewPath,
    assessor: FormalAssessor,
    container: ContainerDep,
) -> FormalReviewModel:
    review = await container.services.reviews.start_review(
        assessor_id=assessor, review_id=review_id
    )
    return FormalReviewModel.model_validate(review.as_dict())


@router.post(
    "/reviews/{review_id}/decision",
    response_model=DecisionResponse,
    summary="Approve the formal assessment, or refer it for further review",
    description=(
        "`APPROVED` opens the certificate gate for this assessment and nothing else, and triggers "
        "the certificate workflow. `REQUIRES_FURTHER_REVIEW` leaves the certificate blocked "
        "permanently — nothing in UC-09 turns an escalation into an approval.\n\n"
        "A decision is final. A second decision returns `409 REVIEW_ALREADY_DECIDED` naming the "
        "assessor who made the first one, and two simultaneous decisions resolve to one.\n\n"
        "The learner notification and the certificate trigger both happen after the decision is "
        "persisted and neither can undo it: an unreachable certificate workflow leaves an approved "
        "assessment with a retriable trigger."
    ),
)
async def submit_decision(
    review_id: ReviewPath,
    assessor: FormalAssessor,
    container: ContainerDep,
    payload: AssessorDecisionRequest,
) -> DecisionResponse:
    outcome = await container.services.reviews.decide(
        assessor_id=assessor,
        review_id=review_id,
        decision=payload.decision,
        notes=payload.notes,
    )
    return DecisionResponse.model_validate(outcome.as_dict())


@router.post(
    "/reviews/{review_id}/certificate-workflow",
    response_model=CertificateTriggerResponse,
    summary="Trigger the certificate workflow for an approved formal assessment",
    description=(
        "The retry path: an approval whose certificate trigger failed because the workflow was "
        "unreachable. Refused with `403 CERTIFICATE_NOT_APPROVED` for anything that is not an "
        "approved formal assessment, and idempotent — an assessment whose workflow has already "
        "been triggered reports a replay rather than requesting a second certificate."
    ),
)
async def trigger_certificate_workflow(
    review_id: ReviewPath,
    assessor: FormalAssessor,
    container: ContainerDep,
) -> CertificateTriggerResponse:
    # Read through the review the assessor is authorised for, rather than accepting a formal attempt
    # id: this
    # endpoint must not become a way to trigger a certificate for an assessment the caller cannot
    # see.
    detail = await container.services.reviews.get_detail(assessor_id=assessor, review_id=review_id)
    outcome = await container.services.certificates.trigger(
        formal_attempt=detail.formal_attempt, review=detail.review
    )
    return CertificateTriggerResponse.model_validate(outcome.as_dict())
