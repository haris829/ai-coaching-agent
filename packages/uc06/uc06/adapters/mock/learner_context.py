"""MockLearnerContextProvider - deterministic learner-context scenarios.

Scenario selection is by session_id prefix, so a test names the behaviour it
wants in the identifier it sends. No randomness, no sleeps.

Covers: every NARIC level; retrieved vs default source; practice area present and
absent; unavailable; timeout; and a level value that maps to no enum member
(which is an invalid response, never a level).
"""

from __future__ import annotations

from typing import Final

from ...config import Settings
from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ...domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from ...domain.models import LearnerContext

#: session_id prefixes selecting a scenario.
PREFIX_LEVEL: Final = "sess-level-"          # sess-level-3, sess-level-7-plus ...
PREFIX_UNAVAILABLE: Final = "sess-ctx-unavailable"
PREFIX_TIMEOUT: Final = "sess-ctx-timeout"
PREFIX_BAD_LEVEL: Final = "sess-ctx-badlevel"
PREFIX_NO_PRACTICE_AREA: Final = "sess-no-practice-area"
PREFIX_NOT_CASE_LINKED: Final = "sess-not-case-linked"
PREFIX_DEFAULTED: Final = "sess-ctx-defaulted"

_LEVEL_BY_SUFFIX: Final[dict[str, NaricLevel]] = {
    "3": NaricLevel.LEVEL_3,
    "4": NaricLevel.LEVEL_4,
    "5": NaricLevel.LEVEL_5,
    "6": NaricLevel.LEVEL_6,
    "7": NaricLevel.LEVEL_7,
    "7-plus": NaricLevel.LEVEL_7_PLUS,
}


#: Conformance scenario map, declared in the adapter (see tests/conformance).
CONFORMANCE_SCENARIOS: Final[dict[str, str]] = {
    "available": "sess-level-7",
    "unavailable": PREFIX_UNAVAILABLE,
    "timeout": PREFIX_TIMEOUT,
    "unmappable_level": PREFIX_BAD_LEVEL,
    "no_practice_area": PREFIX_NO_PRACTICE_AREA,
}


class MockLearnerContextProvider:
    """Implements LearnerContextProvider."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self.calls: list[tuple[str, str]] = []

    def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        self.calls.append((session_id, user_id))

        if session_id.startswith(PREFIX_UNAVAILABLE):
            raise ProviderUnavailable("learner_context_provider", "context_service_unreachable")
        if session_id.startswith(PREFIX_TIMEOUT):
            raise ProviderTimeout("learner_context_provider", "context_read_timeout")
        if session_id.startswith(PREFIX_BAD_LEVEL):
            # Upstream sent something like "LEVEL_8" or "postgraduate". A value
            # mapping to no enum member is an invalid response, not a level, and
            # is never rounded to a neighbour.
            raise ProviderInvalidResponse("learner_context_provider", "naric_level_not_in_enum")

        level = NaricLevel.LEVEL_5
        source = NaricLevelSource.RETRIEVED
        if session_id.startswith(PREFIX_LEVEL):
            suffix = session_id[len(PREFIX_LEVEL):].split("-x")[0]
            level = _LEVEL_BY_SUFFIX.get(suffix, NaricLevel.LEVEL_5)
        if session_id.startswith(PREFIX_DEFAULTED):
            source = NaricLevelSource.DEFAULT

        practice_area = None if session_id.startswith(PREFIX_NO_PRACTICE_AREA) else "criminal"
        case_linked = not session_id.startswith(PREFIX_NOT_CASE_LINKED)

        return LearnerContext(
            session_id=session_id,
            user_id=user_id,
            naric_level=level,
            naric_level_source=source,
            source_status=SourceStatus.AVAILABLE,
            practice_area=practice_area,
            case_linked_mode=case_linked,
            case_file_id=None,
        )
