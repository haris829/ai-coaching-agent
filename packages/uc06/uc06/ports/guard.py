"""GuardClassifier - classifies a question for the outcome-prediction guard."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import GuardResult


@runtime_checkable
class GuardClassifier(Protocol):
    """Classify a learner question.

    GuardResult.guard_class distinguishes at minimum `none`,
    `outcome_prediction` and `litigation_strategy`.

    Fail-safe direction: a classifier that cannot decide must not silently return
    `none`. It raises ProviderUnavailable / ProviderTimeout, and UC-06 then falls
    back to the in-domain rule set (uc06.domain.guard_vocabulary), which is always
    available in-process. The guard is never skipped because a provider is down.

    Failure contract: ProviderUnavailable, ProviderTimeout,
    ProviderInvalidResponse only.
    """

    def classify(self, question: str) -> GuardResult:
        ...
