"""The attempt boundary.

UC-04 needs exactly one thing from UC-03: a *submitted* attempt, with its locked configuration
snapshot and its frozen questions and answers. It never creates, times, unlocks or writes to an
attempt, and the port has no method that could.

Learner scoping lives here rather than in each route handler: the port takes an optional
``learner_id`` and answers ``None`` when the attempt belongs to somebody else, so "a learner can
only see their own result" is enforced at the boundary in one place. UC-03 does the same thing in
its repository layer, for the same reason.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.scoring.integration.attempt_delivery.types import SubmittedAttempt

__all__ = ["AttemptSourcePort", "SubmittedAttempt"]


class AttemptSourcePort(Protocol):
    """Read access to a submitted attempt and the data frozen onto it."""

    def get_attempt(
        self, attempt_id: str, *, learner_id: str | None = None
    ) -> SubmittedAttempt | None:
        """The attempt, or ``None`` when it does not exist or is not this learner's."""
        ...

    def list_submitted_attempt_ids(
        self, *, learner_id: str, quiz_id: str | None = None
    ) -> list[str]:
        """Submitted attempt ids for a learner, newest first. Used for result listings."""
        ...
