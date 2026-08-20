"""Administrator endpoints for additional-attempt grants.

::

    POST …/admin/grants                                     grant extra attempts (idempotent)
    GET  …/admin/learners/{l}/quizzes/{q}/grants             what this learner has been granted
    POST …/admin/grants/{g}/revoke                          withdraw an unused grant

All three sit behind ``require_admin`` — the same guard, environment variable and actor header
UC-02 already uses, so the two branches merge into one administrator seam rather than two.

``POST /grants`` **requires** an idempotency key, in the ``Idempotency-Key`` header or in the body.
Grants are the one operation where a derived key would be wrong: two identical grants a week apart
can both be legitimate, and only the caller knows whether this request is a second decision or a
resent form. Refusing the request without a key is what makes "a retried grant does not grant
twice" a guarantee rather than a hope (§14).

The list response reports ``configured_maximum_attempts`` beside the grants for one reason: to make
it visible that granting did not change it (§11).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Path, Response, status

from app.core.errors import BadRequestError, FieldIssue
from app.modules.retakes.api.dependencies import RetakeAdmin, RetakeCtx
from app.modules.retakes.schemas.requests import CreateGrantRequest, RevokeGrantRequest
from app.modules.retakes.schemas.responses import GrantListResponse, GrantModel, GrantResponse

router = APIRouter(tags=["Administrator grants"])

IDEMPOTENCY_HEADER = "Idempotency-Key"


@router.post(
    "/grants",
    response_model=GrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a learner additional attempts at one quiz",
    description=(
        "Grants extra attempts to one learner on one course and quiz. **The quiz configuration is "
        "not modified** — the course-wide maximum stays exactly as UC-01 published it, and other "
        "learners are unaffected.\n\n"
        "An idempotency key is required, in the `Idempotency-Key` header or the request body. "
        "Repeating the same key returns the existing grant with status **200**; the same key "
        "with a different grant is refused."
    ),
)
async def create_grant(
    payload: CreateGrantRequest,
    admin: RetakeAdmin,
    container: RetakeCtx,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> GrantResponse:
    key = (idempotency_key or payload.idempotency_key or "").strip()
    if not key:
        raise BadRequestError(
            "An idempotency key is required so that a retried grant cannot grant twice.",
            [
                FieldIssue(
                    field=IDEMPOTENCY_HEADER,
                    code="IDEMPOTENCY_KEY_REQUIRED",
                    message=(
                        f"Supply a unique key in the {IDEMPOTENCY_HEADER} header or the "
                        "idempotency_key field."
                    ),
                )
            ],
        )

    grant, replayed = await container.services.grants.grant(
        learner_id=payload.learner_id,
        quiz_id=payload.quiz_id,
        course_id=payload.course_id,
        additional_attempts=payload.additional_attempts,
        reason=payload.reason,
        # The actor comes from the authorisation seam, never from the body.
        granted_by=admin,
        idempotency_key=key,
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return GrantResponse(grant=GrantModel.model_validate(grant.as_dict()), replayed=replayed)


@router.get(
    "/learners/{learner_id}/quizzes/{quiz_id}/grants",
    response_model=GrantListResponse,
    summary="Additional attempts granted to one learner for one quiz",
)
async def list_grants(
    learner_id: Annotated[str, Path()],
    quiz_id: Annotated[str, Path()],
    admin: RetakeAdmin,
    container: RetakeCtx,
) -> GrantListResponse:
    course_id, grants = await container.services.grants.list_for_learner_quiz(
        learner_id, quiz_id
    )
    granted = await container.services.allowances.granted_attempts(
        learner_id, course_id, quiz_id
    )
    active = await container.ports.configurations.get_active_configuration(quiz_id)
    return GrantListResponse(
        learner_id=learner_id,
        quiz_id=quiz_id,
        course_id=course_id,
        configured_maximum_attempts=active.maximum_attempts if active else None,
        granted_attempts=granted,
        grants=[GrantModel.model_validate(item.as_dict()) for item in grants],
    )


@router.post(
    "/grants/{grant_id}/revoke",
    response_model=GrantModel,
    summary="Withdraw a grant whose attempts have not been used",
    description=(
        "Refused once the granted attempts have been used: withdrawing a spent grant would push "
        "the learner's used count above their entitlement. The record is never deleted — "
        "revocation is a status transition, so the audit trail survives."
    ),
)
async def revoke_grant(
    grant_id: Annotated[str, Path()],
    admin: RetakeAdmin,
    container: RetakeCtx,
    payload: RevokeGrantRequest | None = None,
) -> GrantModel:
    body = payload or RevokeGrantRequest()
    grant = await container.services.grants.revoke(
        grant_id=grant_id, revoked_by=admin, reason=body.reason
    )
    return GrantModel.model_validate(grant.as_dict())
