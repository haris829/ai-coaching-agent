"""The port UC-03 uses to reach UC-01 (Quiz Configuration & Rules).

UC-03 depends on this :class:`Protocol` only. The concrete implementation is
supplied at composition time, so UC-01 can arrive as an in-process service, an
HTTP client, or a message-based adapter without any change to UC-03's services.

Implementations are read-only from UC-03's perspective: UC-03 never creates,
mutates, or versions quiz configuration.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.attempt_delivery.integration.uc01.types import (
    QuizAvailability,
    QuizConfigurationVersion,
)


class QuizConfigurationPort(Protocol):
    """Read access to quiz configuration owned by UC-01."""

    def get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None:
        """Return availability, or ``None`` when the quiz does not exist."""
        ...

    def get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None:
        """Return the version active *now*, or ``None`` when there is none.

        UC-03 calls this exactly once per attempt — at creation — and then locks
        the result onto the attempt.
        """
        ...

    def get_configuration_version(
        self, configuration_version_id: str
    ) -> QuizConfigurationVersion | None:
        """Return a specific historical version.

        Used only for reconciliation and diagnostics. Normal attempt operation
        reads the snapshot persisted on the attempt, so an attempt survives its
        version being superseded or withdrawn in UC-01.
        """
        ...
