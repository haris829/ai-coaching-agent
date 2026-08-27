"""MOCK Profile / personalisation adapter — development only.

Implements :class:`uc01.contracts.services.ProfileService`.

An *incomplete* profile is returned as a ``UserProfile`` with empty fields — never as an
error, and never with a placeholder name. Only a genuine failure raises.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ...contracts.exceptions import DependencyUnavailableError, InvalidUpstreamResponseError
from ...domain.models import UserContext, UserProfile
from . import fixtures
from .scenarios import ProfileScenario

logger = logging.getLogger(__name__)

DEPENDENCY = "profile"


class MockProfileAdapter:
    """Fixture-backed profile service."""

    def __init__(self, scenario: ProfileScenario = ProfileScenario.AVAILABLE) -> None:
        self._scenario = scenario

    # -- contract ----------------------------------------------------------- #

    def get_profile(self, user: UserContext) -> UserProfile:
        return self._normalise(user, self._fetch(user))

    # -- "transport" -------------------------------------------------------- #

    def _fetch(self, user: UserContext) -> Mapping[str, Any]:
        scenario = self._scenario
        if scenario is ProfileScenario.UNAVAILABLE:
            raise DependencyUnavailableError(
                DEPENDENCY,
                technical_detail="mock: simulated profile service HTTP 500",
            )
        if scenario is ProfileScenario.INCOMPLETE:
            return {"id": user.user_id, "personal": {}, "prefs": {}, "progress": {}}
        payload = fixtures.PROFILE_PAYLOADS.get(user.user_id)
        if payload is None:
            # No profile row: an incomplete profile, not a failure.
            return {"id": user.user_id, "personal": {}, "prefs": {}, "progress": {}}
        return payload

    # -- normalisation ------------------------------------------------------ #

    def _normalise(self, user: UserContext, payload: Mapping[str, Any]) -> UserProfile:
        if not isinstance(payload, Mapping):
            raise InvalidUpstreamResponseError(
                DEPENDENCY, technical_detail=f"expected object, got {type(payload).__name__}"
            )
        personal = payload.get("personal") or {}
        prefs = payload.get("prefs") or {}
        progress = payload.get("progress") or {}
        if not all(isinstance(part, Mapping) for part in (personal, prefs, progress)):
            raise InvalidUpstreamResponseError(
                DEPENDENCY, technical_detail="profile sub-objects had unexpected types"
            )

        return UserProfile(
            user_id=user.user_id,
            display_name=self._display_name(personal),
            preferred_language=_opt_str(prefs.get("language")),
            current_course_id=_opt_str(progress.get("currentCourseId")),
            current_lesson_id=_opt_str(progress.get("currentLessonId")),
        )

    @staticmethod
    def _display_name(personal: Mapping[str, Any]) -> str | None:
        first = _opt_str(personal.get("firstName"))
        last = _opt_str(personal.get("lastName"))
        parts = [part for part in (first, last) if part]
        # No name available -> None. Nothing is invented here.
        return " ".join(parts) if parts else None


def _opt_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = ["MockProfileAdapter"]
