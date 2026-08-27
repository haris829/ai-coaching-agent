"""Mock Legal Foot Prints provider. Covers every scenario in the scope section 13."""

from __future__ import annotations

from enum import Enum

from uc02.domain.errors import ProviderInvalidResponse, ProviderUnavailable
from uc02.domain.models.enums import SourceName
from uc02.domain.models.provider_records import LegalProfileRecord
from uc02.domain.ports.providers import LegalFootprintsProvider
from uc02.infrastructure.providers.mocks.base import RecordingMock


class LegalScenario(str, Enum):
    COMPLETE = "complete"
    MISSING_SPECIALITY = "missing_speciality"
    MISSING_PRACTICE_AREA = "missing_practice_area"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"


_SPECIALITIES = ("Commercial contracts", "Consumer protection")
_CASE_TYPES = ("Breach of contract", "Unfair terms")


class MockLegalFootprintsProvider(RecordingMock[LegalScenario], LegalFootprintsProvider):
    def __init__(
        self,
        default_scenario: LegalScenario = LegalScenario.COMPLETE,
        overrides: dict[str, LegalScenario] | None = None,
    ) -> None:
        super().__init__(default_scenario, overrides)

    async def get_profile(self, user_id: str) -> LegalProfileRecord:
        scenario = self._record(user_id)
        if scenario is LegalScenario.TIMEOUT:
            await self._hang()
        if scenario is LegalScenario.UNAVAILABLE:
            raise ProviderUnavailable(
                SourceName.LEGAL_PROFILE, "mock: Legal Foot Prints host unreachable"
            )
        if scenario is LegalScenario.INVALID_RESPONSE:
            raise ProviderInvalidResponse(
                SourceName.LEGAL_PROFILE, "mock: speciality field had an unexpected type"
            )
        if scenario is LegalScenario.EMPTY:
            # A learner who has declared nothing. Never guess on their behalf.
            return LegalProfileRecord()
        if scenario is LegalScenario.MISSING_SPECIALITY:
            return LegalProfileRecord(
                speciality_areas=(),
                case_type_preferences=_CASE_TYPES,
                practice_area="Commercial litigation",
            )
        if scenario is LegalScenario.MISSING_PRACTICE_AREA:
            return LegalProfileRecord(
                speciality_areas=_SPECIALITIES,
                case_type_preferences=_CASE_TYPES,
                practice_area=None,
            )
        return LegalProfileRecord(
            speciality_areas=_SPECIALITIES,
            case_type_preferences=_CASE_TYPES,
            practice_area="Commercial litigation",
        )
