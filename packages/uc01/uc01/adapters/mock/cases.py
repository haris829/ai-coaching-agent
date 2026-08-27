"""MOCK Case Prep / Case File adapter — development only.

Implements :class:`uc01.contracts.services.CaseFileService`. Authorization is checked
against ``authorisedLearners`` in the imitation upstream payload; a client-supplied case
id is never trusted.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from ...contracts.exceptions import (
    DependencyUnavailableError,
    InvalidUpstreamResponseError,
    ResourceNotAccessibleError,
)
from ...domain.models import CaseFile, UserContext
from . import fixtures
from .scenarios import CaseScenario

logger = logging.getLogger(__name__)

DEPENDENCY = "cases"


class MockCaseFileAdapter:
    """Fixture-backed Case Prep service."""

    def __init__(self, scenario: CaseScenario = CaseScenario.AVAILABLE) -> None:
        self._scenario = scenario

    # -- contract ----------------------------------------------------------- #

    def list_accessible_case_files(self, user: UserContext) -> Sequence[CaseFile]:
        return tuple(self._normalise(item) for item in self._fetch(user))

    def get_accessible_case_file(self, user: UserContext, case_id: str) -> CaseFile:
        for case_file in self.list_accessible_case_files(user):
            if case_file.case_id == case_id:
                return case_file
        logger.info(
            "cases.access_denied",
            extra={"uc01": {"dependency": DEPENDENCY, "case_id": case_id[:64]}},
        )
        raise ResourceNotAccessibleError(
            DEPENDENCY,
            resource_id=case_id,
            technical_detail="case file not in the caller's authorised set",
        )

    # -- "transport" -------------------------------------------------------- #

    def _fetch(self, user: UserContext) -> Sequence[Mapping[str, Any]]:
        scenario = self._scenario
        if scenario is CaseScenario.UNAVAILABLE:
            raise DependencyUnavailableError(
                DEPENDENCY,
                technical_detail="mock: simulated Case Prep connection reset",
            )
        if scenario is CaseScenario.INVALID:
            records = fixtures.CASES_INVALID_PAYLOAD.get("records")
            if not isinstance(records, list):
                raise InvalidUpstreamResponseError(
                    DEPENDENCY, technical_detail="records envelope was not a list"
                )
            return records
        if scenario is CaseScenario.EMPTY:
            return ()
        return tuple(
            item
            for item in fixtures.CASE_CATALOGUE
            if user.user_id in item.get("authorisedLearners", ())
        )

    # -- normalisation ------------------------------------------------------ #

    @staticmethod
    def _normalise(payload: Mapping[str, Any]) -> CaseFile:
        case_id = payload.get("caseFileId")
        if not isinstance(case_id, str) or not case_id:
            raise InvalidUpstreamResponseError(
                DEPENDENCY, technical_detail="case entry without a usable caseFileId"
            )
        title = payload.get("caseName")
        matter_ref = payload.get("matterRef")
        return CaseFile(
            case_id=case_id,
            title=title if isinstance(title, str) and title else case_id,
            matter_reference=matter_ref if isinstance(matter_ref, str) else None,
        )


__all__ = ["MockCaseFileAdapter"]
