"""Framing selection.

Policy, per (session, concept):

* pick the first strategy from ``FRAMING_ORDER`` that has not been used yet;
* when all six are spent, the set is **exhausted**. UC-04 says so honestly and offers to go
  deeper or move on. It never cycles back to the first framing.

The no-cycling rule is not only a quality choice. Cycling makes "explain differently" an
unlimited enumeration primitive over the lesson's material, which is the content-extraction
path this component has to close.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.enums import FRAMING_ORDER, FramingStrategy
from ..domain.models import FramingAttempt


@dataclass(frozen=True)
class FramingPlan:
    #: Unused strategies in preference order. Empty when exhausted.
    candidates: tuple[FramingStrategy, ...]
    used: tuple[FramingStrategy, ...]
    exhausted: bool


class FramingSelector:
    def __init__(self, order: Sequence[FramingStrategy] = FRAMING_ORDER) -> None:
        if not order:
            raise ValueError("FramingSelector requires at least one strategy")
        self._order = tuple(order)

    @property
    def order(self) -> tuple[FramingStrategy, ...]:
        return self._order

    def plan(self, attempts: Sequence[FramingAttempt]) -> FramingPlan:
        used_set = {attempt.framing for attempt in attempts}
        used_in_order = tuple(f for f in self._order if f in used_set)
        candidates = tuple(f for f in self._order if f not in used_set)
        return FramingPlan(candidates=candidates, used=used_in_order, exhausted=not candidates)
