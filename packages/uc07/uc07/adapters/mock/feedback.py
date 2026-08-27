"""Deterministic mock FeedbackProvider. Read-only, no randomness, no network."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from uc07.domain.enums import SourceStatus
from uc07.domain.errors import (
    PortName,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import FeedbackRecord
from uc07.ports.read_only import FeedbackProvider

_PORT = PortName.FEEDBACK


@dataclass(frozen=True, slots=True)
class MockFeedbackPayload:
    records: tuple[dict[str, Any], ...] = ()
    status: str = SourceStatus.AVAILABLE.value
    failure: str | None = None  # "unavailable" | "timeout" | "invalid"


class MockFeedbackProvider(FeedbackProvider):
    def __init__(self, payload: MockFeedbackPayload) -> None:
        self._payload = payload

    def _guard(self) -> None:
        if self._payload.failure == "unavailable":
            raise ProviderUnavailable(_PORT)
        if self._payload.failure == "timeout":
            raise ProviderTimeout(_PORT)
        if self._payload.failure == "invalid":
            raise ProviderInvalidResponse(_PORT)

    @staticmethod
    def _map(raw: dict[str, Any]) -> FeedbackRecord:
        try:
            rated_at = raw["rated_at"]
            return FeedbackRecord(
                rating_id=raw["rating_id"],
                interaction_id=raw["interaction_id"],
                user_id=raw["user_id"],
                rated_at=(
                    datetime.fromisoformat(rated_at)
                    if isinstance(rated_at, str)
                    else rated_at
                ),
                rating=raw["rating"],
                comment=raw.get("comment"),
            )
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            raise ProviderInvalidResponse(_PORT) from exc

    def for_interactions(self, interaction_ids: Sequence[str]) -> Sequence[FeedbackRecord]:
        self._guard()
        wanted = set(interaction_ids)
        return tuple(
            self._map(raw)
            for raw in self._payload.records
            if raw.get("interaction_id") in wanted
        )

    def status_for_interactions(self, interaction_ids: Sequence[str]) -> SourceStatus:
        self._guard()
        return SourceStatus(self._payload.status)
