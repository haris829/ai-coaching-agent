from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import FourPartAnswer, LearnerContext


@runtime_checkable
class AnswerGenerator(Protocol):
    """Produces the platform's four-part answer.

    Called only on one of the four permitted paths out of Socratic mode, or
    when Socratic mode is off.  A response missing any of the four parts is a
    ``ProviderInvalidResponse``; there is no partial answer.
    """

    async def generate(self, question: str, context: LearnerContext) -> FourPartAnswer:
        ...
