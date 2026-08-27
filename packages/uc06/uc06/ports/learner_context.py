"""LearnerContextProvider - the learner's NARIC level and session mode."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import LearnerContext


@runtime_checkable
class LearnerContextProvider(Protocol):
    """Read the learner context for a session.

    Failure contract: ProviderUnavailable, ProviderTimeout,
    ProviderInvalidResponse only.

    A NARIC value that maps to no member of the platform enum is an INVALID
    RESPONSE (ProviderInvalidResponse), never a level, and never rounded to a
    neighbouring level. UC-06 then applies the platform default (LEVEL_5, source
    `default`) and still answers the question with the disclaimer intact: a
    context failure never removes a safety control.
    """

    def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        ...
