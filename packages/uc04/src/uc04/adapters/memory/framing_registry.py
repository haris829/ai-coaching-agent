"""In-memory FramingRegistry.

Scoped to (session_id, concept_tag). A deterministic record of what has been used - the
generator is never trusted to remember what it already said.
"""

from __future__ import annotations

from datetime import datetime

from ...domain.enums import FramingStrategy
from ...domain.errors import ProviderUnavailable
from ...domain.models import FramingAttempt

PORT = "framing_registry"


class InMemoryFramingRegistry:
    name = "memory"

    def __init__(self) -> None:
        self._attempts: dict[tuple[str, str], list[FramingAttempt]] = {}
        self.always_fail = False

    def _guard(self) -> None:
        if self.always_fail:
            raise ProviderUnavailable(PORT, "framing registry unavailable")

    def used_framings(self, session_id: str, concept_tag: str) -> list[FramingAttempt]:
        self._guard()
        return list(self._attempts.get((session_id, concept_tag), []))

    def record(
        self,
        session_id: str,
        concept_tag: str,
        framing: FramingStrategy,
        fingerprint: str,
        fingerprint_tokens: tuple[str, ...],
        recorded_at: datetime,
    ) -> None:
        self._guard()
        self._attempts.setdefault((session_id, concept_tag), []).append(
            FramingAttempt(
                session_id=session_id,
                concept_tag=concept_tag,
                framing=framing,
                fingerprint=fingerprint,
                fingerprint_tokens=fingerprint_tokens,
                recorded_at=recorded_at,
            )
        )

    def explain_differently_count(self, session_id: str, concept_tag: str) -> int:
        """Zero for the first explanation; one per re-explanation after it."""
        self._guard()
        return max(0, len(self._attempts.get((session_id, concept_tag), [])) - 1)
