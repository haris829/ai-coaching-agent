"""Mock intent classifier.

Applies the documented rules in ``uc05.domain.intent_rules`` directly, so the
mock and the specification cannot drift apart: there is one rule set and the
mock is a thin async wrapper over it.

``force`` and ``script`` exist for state-machine tests that need a specific
intent regardless of wording -- they let a test drive a transition without
first having to find a phrase that produces it.
"""

from __future__ import annotations

import asyncio

from ...domain.enums import IntentKind
from ...domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ...domain.intent_rules import classify_message
from ...domain.models import Dialogue, IntentResult
from ...registry import INTENT_REGISTRY

PORT = "intent_classifier"
SLOW_SECONDS = 30.0


@INTENT_REGISTRY.register("mock")
class MockIntentClassifier:
    def __init__(
        self,
        force: IntentKind | str | None = None,
        script: list[IntentKind | str] | None = None,
        failure: str | None = None,
        **_: object,
    ) -> None:
        self.force = IntentKind(force) if isinstance(force, str) else force
        self._script: list[IntentKind] = [
            IntentKind(item) if isinstance(item, str) else item
            for item in (script or [])
        ]
        self.failure = failure
        self.calls = 0

    async def classify(self, message: str, dialogue_state: Dialogue) -> IntentResult:
        self.calls += 1

        if self.failure == "timeout":
            raise ProviderTimeout(PORT, "scripted timeout")
        if self.failure == "unavailable":
            raise ProviderUnavailable(PORT, "scripted outage")
        if self.failure == "malformed":
            # A classification that maps to no member of UC-05's intent
            # vocabulary is a contract violation, never a nearest-guess.
            raise ProviderInvalidResponse(PORT, "unmappable classification")
        if self.failure == "slow":
            await asyncio.sleep(SLOW_SECONDS)

        if self._script:
            forced = self._script.pop(0)
            return IntentResult(kind=forced, matched_phrase=None, rule="scripted")
        if self.force is not None:
            return IntentResult(kind=self.force, matched_phrase=None, rule="forced")

        outcome = classify_message(message)
        return IntentResult(
            kind=outcome.kind,
            matched_phrase=outcome.matched_phrase,
            rule=outcome.rule,
        )
