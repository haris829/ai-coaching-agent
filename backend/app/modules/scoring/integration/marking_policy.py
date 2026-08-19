"""The one translation from UC-02's authored scoring strategy to UC-04's marking policy.

UC-02 owns the authoring vocabulary and, as its own enums module says, has no interest in another
capability holding opinions about it. UC-04 therefore neither imports that enum into its domain nor
declares a rival copy: it names the policy it applies when marking, and this file -- the only place
in UC-04 that reads UC-02's names -- maps between them.

Both of UC-04's inbound adapters use it, so the mapping exists exactly once.
"""

from __future__ import annotations

from app.modules.question_bank.domain.enums import ScoringStrategy
from app.modules.scoring.domain.answer_key import MarkingPolicy

_BY_STRATEGY: dict[str, MarkingPolicy] = {
    ScoringStrategy.ALL_OR_NOTHING.value: MarkingPolicy.EXACT,
    ScoringStrategy.PARTIAL_CREDIT.value: MarkingPolicy.PARTIAL,
    ScoringStrategy.PARTIAL_CREDIT_WITH_PENALTY.value: MarkingPolicy.PARTIAL_WITH_DEDUCTION,
}


def translate(strategy: str | None) -> MarkingPolicy:
    """Map an authored scoring strategy onto a marking policy.

    An unknown or absent value maps to :attr:`MarkingPolicy.EXACT`. That is the conservative
    direction: it awards marks only for a fully correct response, so a vocabulary UC-04 has not been
    taught yet can never hand out partial credit nobody configured.
    """
    if not strategy:
        return MarkingPolicy.EXACT
    return _BY_STRATEGY.get(str(strategy).strip().upper(), MarkingPolicy.EXACT)
