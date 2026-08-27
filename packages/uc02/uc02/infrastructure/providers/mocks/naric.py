"""Mock NARIC provider. Covers every scenario in the UC-02 scope section 13."""

from __future__ import annotations

from enum import Enum

from uc02.domain.errors import ProviderInvalidResponse, ProviderUnavailable
from uc02.domain.models.enums import SourceName
from uc02.domain.models.provider_records import NaricRecord
from uc02.domain.ports.providers import NaricProvider
from uc02.infrastructure.providers.mocks.base import RecordingMock


class NaricScenario(str, Enum):
    LEVEL_3 = "level_3"
    LEVEL_4 = "level_4"
    LEVEL_5 = "level_5"
    LEVEL_6 = "level_6"
    LEVEL_7 = "level_7"
    LEVEL_7_PLUS = "level_7_plus"
    MISSING_QUALIFICATION = "missing_qualification"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"


_LEVELS: dict[NaricScenario, tuple[int, str]] = {
    NaricScenario.LEVEL_3: (3, "RQF Level 3 (A-level equivalent)"),
    NaricScenario.LEVEL_4: (4, "RQF Level 4 (Certificate of Higher Education)"),
    NaricScenario.LEVEL_5: (5, "RQF Level 5 (Diploma of Higher Education)"),
    NaricScenario.LEVEL_6: (6, "RQF Level 6 (Bachelors degree)"),
    NaricScenario.LEVEL_7: (7, "RQF Level 7 (Masters degree)"),
    NaricScenario.LEVEL_7_PLUS: (8, "RQF Level 8 (Doctoral degree)"),
}


class MockNaricProvider(RecordingMock[NaricScenario], NaricProvider):
    def __init__(
        self,
        default_scenario: NaricScenario = NaricScenario.LEVEL_5,
        overrides: dict[str, NaricScenario] | None = None,
    ) -> None:
        super().__init__(default_scenario, overrides)

    async def get_qualification_level(self, user_id: str) -> NaricRecord:
        scenario = self._record(user_id)
        if scenario is NaricScenario.TIMEOUT:
            await self._hang()
        if scenario is NaricScenario.UNAVAILABLE:
            raise ProviderUnavailable(SourceName.NARIC, "mock: NARIC endpoint refused connection")
        if scenario is NaricScenario.INVALID_RESPONSE:
            raise ProviderInvalidResponse(
                SourceName.NARIC, "mock: level field absent from NARIC payload"
            )
        if scenario is NaricScenario.MISSING_QUALIFICATION:
            # NARIC answered; it simply holds no qualification for this learner.
            # Defaulting is the assembly service's job, not the adapter's.
            return NaricRecord(level=None, raw_level_label=None)
        level, label = _LEVELS[scenario]
        return NaricRecord(level=level, raw_level_label=label)
