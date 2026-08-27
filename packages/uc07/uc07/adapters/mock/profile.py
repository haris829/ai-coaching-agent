"""Deterministic mock LearnerProfileProvider. Read-only.

Speciality status is carried verbatim: ``empty`` (learner genuinely has no
speciality), ``partial`` (some areas retrieved, more may exist) and the failure
modes are all distinct states.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from uc07.domain.enums import SourceStatus
from uc07.domain.errors import (
    PortName,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import LearnerProfile
from uc07.ports.read_only import LearnerProfileProvider

_PORT = PortName.LEARNER_PROFILE


@dataclass(frozen=True, slots=True)
class MockProfilePayload:
    profile: dict[str, Any] | None = None
    failure: str | None = None  # "unavailable" | "timeout" | "invalid"


class MockLearnerProfileProvider(LearnerProfileProvider):
    def __init__(self, payloads: dict[str, MockProfilePayload]) -> None:
        self._payloads = dict(payloads)

    def get_profile(self, user_id: str) -> LearnerProfile:
        payload = self._payloads.get(user_id)
        if payload is None:
            # Unknown learner: a profile with no speciality set. Nothing invented.
            return LearnerProfile(
                user_id=user_id, speciality_status=SourceStatus.EMPTY
            )
        if payload.failure == "unavailable":
            raise ProviderUnavailable(_PORT)
        if payload.failure == "timeout":
            raise ProviderTimeout(_PORT)
        if payload.failure == "invalid":
            raise ProviderInvalidResponse(_PORT)
        raw = payload.profile or {}
        try:
            return LearnerProfile(
                user_id=raw.get("user_id", user_id),
                speciality_areas=tuple(raw.get("speciality_areas", ())),
                speciality_status=raw.get(
                    "speciality_status", SourceStatus.EMPTY.value
                ),
                naric_level=raw.get("naric_level"),
                naric_level_source=raw.get("naric_level_source"),
            )
        except (ValueError, TypeError, ValidationError) as exc:
            raise ProviderInvalidResponse(_PORT) from exc
