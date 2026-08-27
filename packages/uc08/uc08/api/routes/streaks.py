"""Streak, badge and freeze endpoints.

No route takes a user identifier. The account comes from the identity port.
"""

from __future__ import annotations

from fastapi import APIRouter

from uc08.api.deps import BadgeServiceDep, ContainerDep, CurrentUser, StreakServiceDep
from uc08.api.schemas import (
    AcceptFreezeRequest,
    BadgeCollectionResponse,
    RecordActivityRequest,
    RecordActivityResponse,
    StreakStateResponse,
    freeze_offer_response,
    streak_response,
)
from uc08.application.session import resolve_session_id

router = APIRouter(prefix="/api/v1", tags=["streaks"])


@router.post("/streaks/record-activity", response_model=RecordActivityResponse)
def record_activity(
    payload: RecordActivityRequest,
    user_id: CurrentUser,
    streaks: StreakServiceDep,
    container: ContainerDep,
) -> RecordActivityResponse:
    """Record a coaching interaction. Idempotent on ``interaction_id``."""
    session = resolve_session_id(
        payload.session_id,
        user_id=user_id,
        now=container.clock.now(),
        allow_dev_minting=container.settings.allow_dev_session_minting,
    )
    result = streaks.record_activity(
        user_id=user_id,
        interaction_id=payload.interaction_id,
        session=session,
    )
    return RecordActivityResponse(
        streak=streak_response(result.streak),
        outcome=result.outcome,
        persistence_outcome=result.persistence_outcome,
        idempotent_replay=result.idempotent_replay,
        session_id=result.session_id,
        session_id_source=result.session_id_source,
        activity_status=result.activity_status,
        question_count=result.question_count,
        question_count_status=result.question_count_status,
        awarded_badges=result.awarded_badges,
        badge_events=result.badge_events,
        freeze_offer=freeze_offer_response(result.freeze_offer),
    )


@router.get("/streaks", response_model=StreakStateResponse)
def get_streak(
    user_id: CurrentUser,
    streaks: StreakServiceDep,
    container: ContainerDep,
) -> StreakStateResponse:
    state = streaks.get_state(user_id)
    return StreakStateResponse(
        streak=streak_response(state.streak),
        freeze_offer=freeze_offer_response(state.open_freeze_offer),
        badges=state.badges,
        window_hours=container.settings.streak_window_hours,
        freeze_min_streak_days=container.settings.freeze_min_streak_days,
    )


@router.post("/streaks/freeze", response_model=StreakStateResponse)
def accept_freeze(
    user_id: CurrentUser,
    streaks: StreakServiceDep,
    container: ContainerDep,
    payload: AcceptFreezeRequest = AcceptFreezeRequest(),
) -> StreakStateResponse:
    """Accept an offered streak freeze."""
    state = streaks.accept_freeze(user_id)
    return StreakStateResponse(
        streak=streak_response(state.streak),
        freeze_offer=freeze_offer_response(state.open_freeze_offer),
        badges=state.badges,
        window_hours=container.settings.streak_window_hours,
        freeze_min_streak_days=container.settings.freeze_min_streak_days,
    )


@router.get("/badges", response_model=BadgeCollectionResponse)
def get_badges(
    user_id: CurrentUser,
    badges: BadgeServiceDep,
    container: ContainerDep,
) -> BadgeCollectionResponse:
    return BadgeCollectionResponse(
        badges=badges.held(user_id),
        milestones=container.settings.badge_milestones,
    )
