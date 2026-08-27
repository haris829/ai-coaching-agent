from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import Dialogue, GuidingQuestionResult, LearnerContext


@runtime_checkable
class GuidingQuestionGenerator(Protocol):
    """Produces the next guiding question for a dialogue.

    The generator is handed the **persisted dialogue state** rather than being
    trusted to remember the conversation; UC-05 never delegates state to a
    generator's memory.

    A generator that returns a direct answer when a guiding question was
    requested is a contract violation.  The adapter -- or, as a backstop, the
    application's ``GuidingQuestionGuard`` -- raises
    ``ProviderInvalidResponse``.  Such output is never passed through.
    """

    async def generate(
        self,
        dialogue_state: Dialogue,
        question: str,
        context: LearnerContext,
    ) -> GuidingQuestionResult:
        ...
