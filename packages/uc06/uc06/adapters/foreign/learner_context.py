"""ForeignLearnerContextAdapter - Mattersphere learner directory.

The upstream represents attainment as an "eqfBand" string ("band-seven-plus"),
not as the platform enum. Normalisation happens here and only here. A band that
maps to no member is ProviderInvalidResponse - never a nearest neighbour and
never the platform default, because substituting a level here would make
naric_level_source="retrieved" a lie.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ...domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from ...domain.models import LearnerContext
from . import _upstream

PORT_NAME = "learner_context_provider"

_BAND_TO_LEVEL = {
    "band-three": NaricLevel.LEVEL_3,
    "band-four": NaricLevel.LEVEL_4,
    "band-five": NaricLevel.LEVEL_5,
    "band-six": NaricLevel.LEVEL_6,
    "band-seven": NaricLevel.LEVEL_7,
    "band-seven-plus": NaricLevel.LEVEL_7_PLUS,
}

_ORIGIN_TO_SOURCE = {
    "ASSESSED": NaricLevelSource.RETRIEVED,
    "DECLARED": NaricLevelSource.RETRIEVED,
    "PLATFORM_DEFAULT": NaricLevelSource.DEFAULT,
}


#: Conformance scenario map, declared in the adapter (see tests/conformance).
CONFORMANCE_SCENARIOS: dict[str, str] = {
    "available": "ms-session-1",
    "unavailable": "ms-down-1",
    "timeout": "ms-slow-1",
    "unmappable_level": "ms-unknown-band",
    "no_practice_area": "ms-no-specialism-1",
}


class ForeignLearnerContextAdapter:
    """Implements LearnerContextProvider."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        try:
            raw = _upstream.fetch_learner(session_id, user_id)
        except _upstream.MatterSphereError as exc:
            raise ProviderUnavailable(PORT_NAME, "context_service_unreachable") from exc
        except TimeoutError as exc:
            raise ProviderTimeout(PORT_NAME, "context_read_timeout") from exc

        profile = raw.get("envelope", {}).get("profile")
        if not isinstance(profile, dict):
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_context_payload")

        level = _BAND_TO_LEVEL.get(str(profile.get("eqfBand")))
        if level is None:
            raise ProviderInvalidResponse(PORT_NAME, "naric_level_not_in_enum")

        source = _ORIGIN_TO_SOURCE.get(str(profile.get("bandOrigin")), NaricLevelSource.RETRIEVED)
        specialism = profile.get("specialism")

        return LearnerContext(
            session_id=session_id,
            user_id=user_id,
            naric_level=level,
            naric_level_source=source,
            source_status=SourceStatus.AVAILABLE,
            practice_area=str(specialism).lower() if specialism else None,
            case_linked_mode=str(profile.get("sessionMode")) == "CASE_LINKED",
            case_file_id=None,
        )
