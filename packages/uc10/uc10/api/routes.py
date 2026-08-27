"""HTTP surface.

Exactly the endpoints the specification names:

    POST  /api/v1/interactions/{id}/rating
    GET   /api/v1/interactions/{id}/rating
    GET   /api/v1/admin/flags
    PATCH /api/v1/admin/flags/{id}
    GET   /api/v1/healthz

``user_id`` is resolved server-side on every path and is never accepted from a request
body.  Admin routes depend on a different port with a different credential, so no learner
request can reach them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from uc10.api.deps import (
    AdminDep,
    ContainerDep,
    CurrentUserDep,
    FeedbackDep,
    FlaggingDep,
    describe_wiring,
)
from uc10.api.errors import api_error
from uc10.api.schemas import (
    CurrentRatingResponse,
    FlagListResponse,
    FlagStatusPatch,
    FlagView,
    HealthResponse,
    RatingAcceptedResponse,
    RatingRequest,
    RatingView,
)
from uc10.application.flagging_service import FlagNotFound, InvalidStatusTransition
from uc10.application.results import RatingCaptureResult, RatingCaptureStatus

router = APIRouter()

_HTTP_STATUS_BY_CAPTURE: dict[RatingCaptureStatus, int] = {
    RatingCaptureStatus.RECORDED: status.HTTP_201_CREATED,
    RatingCaptureStatus.REPLACED: status.HTTP_200_OK,
    RatingCaptureStatus.REJECTED_ANONYMOUS: status.HTTP_401_UNAUTHORIZED,
    RatingCaptureStatus.REJECTED_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    RatingCaptureStatus.REJECTED_WINDOW_EXPIRED: status.HTTP_409_CONFLICT,
    RatingCaptureStatus.FAILED_RETRYABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    RatingCaptureStatus.FAILED_PERMANENT: status.HTTP_502_BAD_GATEWAY,
}


def _fail(result: RatingCaptureResult):
    return api_error(
        _HTTP_STATUS_BY_CAPTURE[result.status],
        result.status.value,
        result.message,
        retryable=result.retryable,
    )


def require_learner(user_id: CurrentUserDep) -> str:
    if user_id is None:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED, "rejected_anonymous", "Sign in to leave feedback."
        )
    return user_id


def require_admin(admin_id: AdminDep) -> str:
    """Admin authority comes from its own port. A learner credential never satisfies it."""
    if admin_id is None:
        raise api_error(
            status.HTTP_403_FORBIDDEN, "admin_required", "Administrator access required."
        )
    return admin_id


@router.post(
    "/api/v1/interactions/{interaction_id}/rating",
    response_model=RatingAcceptedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["ratings"],
)
def create_or_replace_rating(
    interaction_id: str,
    body: RatingRequest,
    response: Response,
    feedback: FeedbackDep,
    user_id: CurrentUserDep,
) -> RatingAcceptedResponse:
    """Rate any response: an answer, a redirect, a refusal, a clarifying question or a
    degraded fallback. No response category is excluded."""
    result = feedback.capture(
        interaction_id=interaction_id,
        user_id=user_id,
        rating=body.rating,
        comment=body.comment,
    )
    if not result.ok or result.rating is None:
        raise _fail(result)
    response.status_code = _HTTP_STATUS_BY_CAPTURE[result.status]
    return RatingAcceptedResponse(
        status=result.status.value,
        message=result.message,
        rating=RatingView.of(result.rating),
        superseded_rating_id=result.superseded_rating_id,
    )


@router.get(
    "/api/v1/interactions/{interaction_id}/rating",
    response_model=CurrentRatingResponse,
    tags=["ratings"],
)
def read_own_rating(
    interaction_id: str,
    feedback: FeedbackDep,
    user_id: str = Depends(require_learner),
) -> CurrentRatingResponse:
    """The caller's own rating. Another learner's rating is never returned."""
    record = feedback.current_rating(interaction_id=interaction_id, user_id=user_id)
    return CurrentRatingResponse(
        interaction_id=interaction_id,
        rating=RatingView.of(record) if record else None,
    )


@router.get("/api/v1/admin/flags", response_model=FlagListResponse, tags=["admin"])
def list_open_flags(
    flagging: FlaggingDep,
    _admin_id: str = Depends(require_admin),
) -> FlagListResponse:
    flags = [FlagView.of(flag) for flag in flagging.list_open_flags()]
    return FlagListResponse(flags=flags, count=len(flags))


@router.patch("/api/v1/admin/flags/{flag_id}", response_model=FlagView, tags=["admin"])
def update_flag_status(
    flag_id: str,
    body: FlagStatusPatch,
    flagging: FlaggingDep,
    _admin_id: str = Depends(require_admin),
) -> FlagView:
    try:
        return FlagView.of(flagging.set_status(flag_id, body.status))
    except FlagNotFound as exc:
        raise api_error(
            status.HTTP_404_NOT_FOUND, "flag_not_found", "That flag could not be found."
        ) from exc
    except InvalidStatusTransition as exc:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "invalid_status_transition",
            f"A flag cannot move from {exc.current.value} to {exc.requested.value}.",
        ) from exc


@router.get("/api/v1/healthz", response_model=HealthResponse, tags=["ops"])
def healthz(container: ContainerDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        component="uc10-feedback-improvement",
        wiring={k: str(v) for k, v in describe_wiring(container).items()},
    )
