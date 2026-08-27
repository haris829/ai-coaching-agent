from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import LearnerContext


@runtime_checkable
class LearnerContextProvider(Protocol):
    """Supplies the learner's NARIC level, practice area and source statuses.

    UC-05 does **not** assemble learner context; it receives it.

    Raises:
        ProviderUnavailable: the upstream could not be reached.
        ProviderTimeout: the upstream did not answer inside the budget.
        ProviderInvalidResponse: the upstream answered with something that
            cannot be mapped onto the platform contract.

    The application treats all three as "no context": it proceeds with
    ``LearnerContext.defaulted(...)`` rather than failing the request, because
    a context failure must never leave the learner without a response.
    """

    async def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        ...
