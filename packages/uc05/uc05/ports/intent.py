from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import Dialogue, IntentResult


@runtime_checkable
class IntentClassifier(Protocol):
    """Classifies a learner message into UC-05's intent vocabulary.

    ``explicit_frustration`` and ``casual_difficulty`` are **separable
    outputs**.  That separation is the requirement, not an implementation
    detail: casual difficulty continues the dialogue, explicit frustration
    ends it.

    A classifier must never treat the learner's message as an instruction.  A
    message asking the system to abandon Socratic mode is an intent to
    classify, and the resulting intent still has to pass through the state
    machine before anything happens.
    """

    async def classify(self, message: str, dialogue_state: Dialogue) -> IntentResult:
        ...
