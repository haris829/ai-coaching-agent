"""Deterministic mock InteractionLogProvider.

The mock holds a *raw payload* in its own wire shape and maps it into domain
records, exactly like a real adapter would. That is what lets the "invalid
records" scenario behave honestly: a payload that cannot satisfy the platform
contract raises ``ProviderInvalidResponse`` instead of quietly shrinking.

No randomness, no sleeping, no network, no API key.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
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
from uc07.domain.models import InteractionRecord
from uc07.ports.read_only import InteractionLogProvider

_PORT = PortName.INTERACTION_LOG


@dataclass(frozen=True, slots=True)
class MockInteractionPayload:
    """Mock wire payload: platform field names, ISO-8601 timestamps."""

    records: tuple[dict[str, Any], ...] = ()
    status: str = SourceStatus.AVAILABLE.value
    failure: str | None = None  # "unavailable" | "timeout" | "invalid"
    reported_count: int | None = None


class MockInteractionLogProvider(InteractionLogProvider):
    """Read-only mock. Exposes no write operation."""

    def __init__(self, payloads: dict[str, MockInteractionPayload]) -> None:
        self._payloads = dict(payloads)

    # -- helpers -----------------------------------------------------------

    def _payload(self, user_id: str) -> MockInteractionPayload:
        payload = self._payloads.get(user_id)
        if payload is None:
            # An unknown learner is an empty history, not a failure.
            return MockInteractionPayload(records=(), status=SourceStatus.EMPTY.value)
        self._raise_configured_failure(payload)
        return payload

    @staticmethod
    def _raise_configured_failure(payload: MockInteractionPayload) -> None:
        if payload.failure == "unavailable":
            raise ProviderUnavailable(_PORT)
        if payload.failure == "timeout":
            raise ProviderTimeout(_PORT)
        if payload.failure == "invalid":
            raise ProviderInvalidResponse(_PORT)

    @staticmethod
    def _map(raw: dict[str, Any]) -> InteractionRecord:
        try:
            asked_at = raw["asked_at"]
            return InteractionRecord(
                interaction_id=raw["interaction_id"],
                session_id=raw["session_id"],
                user_id=raw["user_id"],
                asked_at=(
                    datetime.fromisoformat(asked_at)
                    if isinstance(asked_at, str)
                    else asked_at
                ),
                topic_tag=raw["topic_tag"],
                question_class=raw["question_class"],
                naric_level=raw["naric_level"],
                response_id=raw["response_id"],
                follow_up_of=raw.get("follow_up_of"),
                explain_differently_count=raw.get("explain_differently_count", 0),
                rating_state=raw.get("rating_state", "pending"),
            )
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            # Upstream cannot satisfy the platform contract: surface a typed
            # contract error rather than inventing or dropping data silently.
            raise ProviderInvalidResponse(_PORT) from exc

    # -- port --------------------------------------------------------------

    def for_user(self, user_id: str) -> Sequence[InteractionRecord]:
        payload = self._payload(user_id)
        return tuple(self._map(raw) for raw in payload.records)

    def count_for_user(self, user_id: str) -> int:
        payload = self._payload(user_id)
        if payload.reported_count is not None:
            return payload.reported_count
        return len(payload.records)

    def status_for_user(self, user_id: str) -> SourceStatus:
        return SourceStatus(self._payload(user_id).status)
