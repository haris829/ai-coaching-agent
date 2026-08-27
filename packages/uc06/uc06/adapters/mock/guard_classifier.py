"""MockGuardClassifier - deterministic classification over the in-domain rules.

"Mock" names its position in the registry, not the quality of its logic: the rule
set in uc06.domain.guard_vocabulary is the real, shipped classifier and is what a
remote classifier would have to beat. UC-06 always retains it as the fallback, so
the guard cannot be skipped because a provider is down.

Failure scenarios are triggered by a sentinel prefix on the question so that the
resilience path can be exercised deterministically.
"""

from __future__ import annotations

from typing import Final

from ...config import Settings
from ...domain.errors import ProviderTimeout, ProviderUnavailable
from ...domain.guard_vocabulary import classify_question
from ...domain.legal_tests import resolve_topic
from ...domain.models import GuardResult

TRIGGER_UNAVAILABLE: Final = "__guard_unavailable__"
TRIGGER_TIMEOUT: Final = "__guard_timeout__"


class MockGuardClassifier:
    """Implements GuardClassifier."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self.calls: int = 0

    def classify(self, question: str) -> GuardResult:
        self.calls += 1
        if TRIGGER_UNAVAILABLE in question:
            raise ProviderUnavailable("guard_classifier", "classifier_unreachable")
        if TRIGGER_TIMEOUT in question:
            raise ProviderTimeout("guard_classifier", "classifier_timeout")
        guard_class, rule_id = classify_question(question)
        return GuardResult(
            guard_class=guard_class,
            matched_rule_id=rule_id,
            topic_tag=resolve_topic(question).topic_tag,
        )
