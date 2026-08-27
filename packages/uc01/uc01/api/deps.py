"""FastAPI dependencies.

Identity is resolved here, from headers only. No endpoint accepts a user id as input,
so no handler can be tricked into acting as another user.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request

from ..adapters.mock.scenarios import ScenarioSet, parse_scenario_header
from ..application.session_service import SessionInitiationService
from ..config import Settings
from ..domain.models import UserContext
from .container import AppContainer
from .schemas import MockNoticeOut

logger = logging.getLogger(__name__)

SCENARIO_HEADER = "X-Dev-Scenarios"


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


def get_settings(request: Request) -> Settings:
    return request.app.state.container.settings


def get_scenarios(request: Request) -> ScenarioSet:
    """Resolve the mock scenarios for this request.

    The ``X-Dev-Scenarios`` header is a **development affordance** for exercising every
    UI state. It is ignored unless dev mode *and* the header switch are enabled, and it
    can only choose between mock fixtures — it can never affect identity, authorization,
    or the recorded ``naric_level_source``.
    """
    container: AppContainer = request.app.state.container
    settings = container.settings
    configured = settings.scenarios
    raw = request.headers.get(SCENARIO_HEADER)
    if not raw:
        return configured
    if not settings.dev_scenario_header_enabled:
        logger.info("dev.scenario_header_ignored", extra={"uc01": {"reason": "disabled"}})
        return configured
    resolved = configured.merged_with(parse_scenario_header(raw))
    logger.info(
        "dev.scenario_header_applied", extra={"uc01": {"scenarios": dict(resolved.describe())}}
    )
    return resolved


def get_current_user(request: Request) -> UserContext:
    """Resolve the caller from the ``Authorization`` or ``X-Dev-User`` header.

    Raises :class:`~uc01.domain.errors.AuthenticationRequiredError`, which the app's
    exception handler turns into a 401 with a safe message.
    """
    container: AppContainer = request.app.state.container
    credential = request.headers.get("Authorization") or request.headers.get("X-Dev-User")
    return container.identity.resolve(credential)


def get_service(
    request: Request,
    scenarios: Annotated[ScenarioSet, Depends(get_scenarios)],
) -> SessionInitiationService:
    container: AppContainer = request.app.state.container
    return container.service(scenarios)


def integrations_notice(settings: Settings) -> MockNoticeOut:
    """Describe which adapters are in use, so mocks are never presented as real."""
    adapters = dict(settings.describe_adapters())
    using_mocks = settings.uses_only_mock_adapters
    return MockNoticeOut(
        using_mock_adapters=using_mocks,
        adapters=adapters,
        warning=(
            "Development mocks are active: NARIC, Courses, Case Prep and Profile data "
            "are fixtures, not real integrations."
            if using_mocks
            else None
        ),
    )


CurrentUser = Annotated[UserContext, Depends(get_current_user)]
Service = Annotated[SessionInitiationService, Depends(get_service)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

__all__ = [
    "CurrentUser",
    "SCENARIO_HEADER",
    "Service",
    "SettingsDep",
    "get_container",
    "get_current_user",
    "get_scenarios",
    "get_service",
    "get_settings",
    "integrations_notice",
]
