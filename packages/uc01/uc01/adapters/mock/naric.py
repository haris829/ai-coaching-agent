"""MOCK NARIC adapter — development only.

Implements :class:`uc01.contracts.services.NaricService`. All normalisation from the
imitation upstream payload to :class:`uc01.domain.models.NaricAssessment` happens here,
which is precisely the code a real adapter replaces.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ...contracts.exceptions import DependencyUnavailableError, InvalidUpstreamResponseError
from ...domain.enums import NaricAssessmentState
from ...domain.models import NaricAssessment, UserContext
from . import fixtures
from .scenarios import NaricScenario

logger = logging.getLogger(__name__)

DEPENDENCY = "naric"

_STATUS_MAP: Mapping[str, NaricAssessmentState] = {
    "COMPLETED": NaricAssessmentState.COMPLETE,
    "COMPLETE": NaricAssessmentState.COMPLETE,
    "PARTIAL": NaricAssessmentState.INCOMPLETE,
    "INCOMPLETE": NaricAssessmentState.INCOMPLETE,
    "IN_CALIBRATION": NaricAssessmentState.CALIBRATING,
    "CALIBRATING": NaricAssessmentState.CALIBRATING,
}


class MockNaricAdapter:
    """Fixture-backed NARIC service."""

    def __init__(self, scenario: NaricScenario = NaricScenario.PER_USER) -> None:
        self._scenario = scenario

    # -- contract ----------------------------------------------------------- #

    def get_assessment(self, user: UserContext) -> NaricAssessment:
        payload = self._fetch(user)
        return self._normalise(payload)

    # -- "transport" -------------------------------------------------------- #

    def _fetch(self, user: UserContext) -> Mapping[str, Any]:
        scenario = self._scenario
        if scenario is NaricScenario.UNAVAILABLE:
            # A real adapter would catch a transport error here and raise the same
            # contract exception with the technical detail attached.
            raise DependencyUnavailableError(
                DEPENDENCY,
                technical_detail="mock: simulated NARIC transport failure (HTTP 503)",
            )
        if scenario is NaricScenario.INVALID:
            return fixtures.NARIC_INVALID_PAYLOAD
        if scenario is NaricScenario.SUCCESS:
            return fixtures.NARIC_SUCCESS_PAYLOAD
        if scenario is NaricScenario.INCOMPLETE:
            return fixtures.NARIC_INCOMPLETE_PAYLOAD
        if scenario is NaricScenario.CALIBRATING:
            return fixtures.NARIC_CALIBRATING_PAYLOAD

        payload = fixtures.NARIC_PAYLOADS.get(user.user_id)
        if payload is None:
            # No assessment on record for this learner: a legitimate incomplete state,
            # not an error.
            return {"assessmentStatus": "PARTIAL", "result": {"explanationLevel": None}}
        return payload

    # -- normalisation ------------------------------------------------------ #

    def _normalise(self, payload: Mapping[str, Any]) -> NaricAssessment:
        if not isinstance(payload, Mapping):
            raise InvalidUpstreamResponseError(
                DEPENDENCY, technical_detail=f"expected object, got {type(payload).__name__}"
            )

        raw_status = payload.get("assessmentStatus")
        state = _STATUS_MAP.get(str(raw_status).upper()) if raw_status is not None else None
        if state is None:
            logger.warning(
                "naric.normalise.unknown_status",
                extra={"uc01": {"dependency": DEPENDENCY, "raw_status": str(raw_status)[:64]}},
            )
            raise InvalidUpstreamResponseError(
                DEPENDENCY,
                technical_detail=f"unknown assessmentStatus {str(raw_status)[:64]!r}",
            )

        result = payload.get("result") or {}
        if not isinstance(result, Mapping):
            raise InvalidUpstreamResponseError(
                DEPENDENCY, technical_detail="result was not an object"
            )

        level = self._coerce_level(result.get("explanationLevel"))
        if state is NaricAssessmentState.COMPLETE and level is None:
            # "Completed" with no usable level is an invalid response, not a level of 5.
            raise InvalidUpstreamResponseError(
                DEPENDENCY,
                technical_detail=(
                    "assessmentStatus=COMPLETED but explanationLevel could not be "
                    f"parsed from {result.get('explanationLevel')!r}"
                ),
            )

        return NaricAssessment(
            state=state,
            level=level,
            assessed_at=self._coerce_timestamp(result.get("assessedAt")),
            detail_code=self._detail_code(payload),
        )

    @staticmethod
    def _coerce_level(raw: Any) -> int | None:
        if raw is None or isinstance(raw, bool):
            return None
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_timestamp(raw: Any) -> datetime | None:
        if not isinstance(raw, str) or not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            # A bad timestamp is not worth failing a whole assessment over.
            logger.info("naric.normalise.bad_timestamp", extra={"uc01": {"raw": raw[:32]}})
            return None

    @staticmethod
    def _detail_code(payload: Mapping[str, Any]) -> str | None:
        missing = payload.get("missingSections")
        if isinstance(missing, list) and missing:
            return "missing_sections:" + ",".join(str(item)[:32] for item in missing[:5])
        return None


__all__ = ["MockNaricAdapter"]
