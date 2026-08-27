"""Composition root.

Every dependency is assembled here and injected through FastAPI ``Depends``.  No DI
framework.  Nothing below this file knows which implementation of a port it received.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request

from uc10.adapters.memory.repositories import (
    InMemoryFlagRepository,
    InMemoryFlagWorkQueue,
    InMemoryRatingRepository,
)
from uc10.adapters.memory.support import (
    RecordingAdminNotificationSink,
    SettingsThresholdConfigProvider,
    SystemClock,
)
from uc10.adapters.mock.identity import ConfiguredAdminIdentityProvider, HeaderCurrentUserProvider
from uc10.adapters.registry import ProviderContext, build_interaction_provider
from uc10.application.feedback_facade import FeedbackFacade
from uc10.application.flagging_service import FlaggingService
from uc10.application.rating_service import RatingService
from uc10.config import Settings, get_settings
from uc10.domain.ids import new_dev_session_id
from uc10.ports.admin_notification_sink import AdminNotificationSink
from uc10.ports.clock import Clock
from uc10.ports.current_user_provider import AdminIdentityProvider, CurrentUserProvider
from uc10.ports.flag_repository import FlagRepository
from uc10.ports.flag_work_queue import FlagWorkQueue
from uc10.ports.interaction_provider import InteractionProvider
from uc10.ports.rating_repository import RatingRepository
from uc10.ports.threshold_config_provider import ThresholdConfigProvider


@dataclass
class Container:
    settings: Settings
    clock: Clock
    interactions: InteractionProvider
    ratings_repository: RatingRepository
    flag_repository: FlagRepository
    flag_work_queue: FlagWorkQueue
    notifications: AdminNotificationSink
    policy_config: ThresholdConfigProvider
    current_user: CurrentUserProvider
    admin_identity: AdminIdentityProvider
    flagging: FlaggingService
    ratings: RatingService
    feedback: FeedbackFacade


def build_container(
    *,
    settings: Settings | None = None,
    clock: Clock | None = None,
    interactions: InteractionProvider | None = None,
    ratings_repository: RatingRepository | None = None,
    flag_repository: FlagRepository | None = None,
    flag_work_queue: FlagWorkQueue | None = None,
    notifications: AdminNotificationSink | None = None,
    policy_config: ThresholdConfigProvider | None = None,
    current_user: CurrentUserProvider | None = None,
    admin_identity: AdminIdentityProvider | None = None,
) -> Container:
    """Assemble the component.

    Overrides exist for tests and for the conformance harness.  In normal operation only
    ``settings`` is supplied and every adapter comes from the registry or from the
    lightweight local defaults.
    """
    settings = settings or get_settings()
    clock = clock or SystemClock()
    # Registry lookup. An unregistered provider key raises here, at startup.
    interactions = interactions or build_interaction_provider(
        ProviderContext(settings=settings, clock=clock)
    )
    ratings_repository = ratings_repository or InMemoryRatingRepository()
    flag_repository = flag_repository or InMemoryFlagRepository()
    flag_work_queue = flag_work_queue or InMemoryFlagWorkQueue(now_factory=clock.now)
    notifications = notifications or RecordingAdminNotificationSink()
    policy_config = policy_config or SettingsThresholdConfigProvider()
    current_user = current_user or HeaderCurrentUserProvider()
    admin_identity = admin_identity or ConfiguredAdminIdentityProvider()

    flagging = FlaggingService(
        ratings=ratings_repository,
        flags=flag_repository,
        work_queue=flag_work_queue,
        notifications=notifications,
        config=policy_config,
        clock=clock,
    )
    rating_service = RatingService(
        interactions=interactions,
        ratings=ratings_repository,
        clock=clock,
        config=policy_config,
        on_rating_recorded=flagging.evaluate_topic,
    )
    return Container(
        settings=settings,
        clock=clock,
        interactions=interactions,
        ratings_repository=ratings_repository,
        flag_repository=flag_repository,
        flag_work_queue=flag_work_queue,
        notifications=notifications,
        policy_config=policy_config,
        current_user=current_user,
        admin_identity=admin_identity,
        flagging=flagging,
        ratings=rating_service,
        feedback=FeedbackFacade(rating_service),
    )


# ------------------------------------------------------------------ FastAPI wiring


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_feedback(container: ContainerDep) -> FeedbackFacade:
    return container.feedback


def get_flagging(container: ContainerDep) -> FlaggingService:
    return container.flagging


def get_current_user_id(request: Request, container: ContainerDep) -> str | None:
    """Learner identity, resolved server-side. Never read from a request body."""
    return container.current_user.resolve(request)


def get_admin_id(request: Request, container: ContainerDep) -> str | None:
    """Admin authority: a separate port with a separate credential."""
    return container.admin_identity.resolve_admin(request)


FeedbackDep = Annotated[FeedbackFacade, Depends(get_feedback)]
FlaggingDep = Annotated[FlaggingService, Depends(get_flagging)]
CurrentUserDep = Annotated[str | None, Depends(get_current_user_id)]
AdminDep = Annotated[str | None, Depends(get_admin_id)]


def mint_dev_session_id(settings: Settings) -> str:
    """Dev-mode session minting, gated by configuration and defaulted off.

    This component receives an opaque ``session_id`` from the platform and never creates
    one on a production path. Nothing in the request path calls this.
    """
    if not settings.allow_dev_session_minting:
        raise RuntimeError(
            "session minting is disabled: this component receives an opaque session_id "
            "and never creates one on a production path"
        )
    return new_dev_session_id()


def describe_wiring(container: Container) -> dict[str, Any]:
    """Non-secret wiring description for the health endpoint."""
    return {
        "interaction_provider": container.settings.interaction_provider,
        "interaction_adapter": type(container.interactions).__name__,
        "rating_repository": type(container.ratings_repository).__name__,
        "flag_repository": type(container.flag_repository).__name__,
        "clock": type(container.clock).__name__,
    }
