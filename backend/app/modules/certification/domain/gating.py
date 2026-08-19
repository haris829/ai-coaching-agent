"""The gating rules: pass/fail, remaining attempts, and whether a certificate is due.

Pure functions with no dependencies, so every boundary case is testable without a database, a clock
or a service. Small enough to inline at the call site and important enough not to -- a ``>`` where a
``>=`` belongs, or a remaining count that forgets it cannot go negative, would be a defect in a
certificate gate rather than a cosmetic bug.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.certification.domain.enums import Outcome


def decide(percentage: float, pass_mark_percentage: float) -> Outcome:
    """PASS when the percentage **reaches** the pass mark.

    Inclusive on purpose: a pass mark of 70 means 70% passes. UC-04 rounds the percentage to two
    decimals before storing it, so this compares exactly the number the learner is shown -- there is
    no case where a result reads "70%" and is recorded as a fail.
    """
    return Outcome.PASS if percentage >= pass_mark_percentage else Outcome.FAIL


def attempts_remaining(max_attempts: int | None, attempts_used: int) -> int | None:
    """Attempts left for this learner on this quiz.

    ``None`` means unlimited, which is what a configuration with no maximum means and is *not* the
    same as zero. Never negative: if an allowance was lowered after attempts were used, the answer
    is zero rather than a negative number some client would render as "-2 attempts left".
    """
    if max_attempts is None:
        return None
    return max(0, max_attempts - max(0, attempts_used))


@dataclass(frozen=True, slots=True)
class Gate:
    """The complete gating decision for one attempt."""

    outcome: Outcome
    percentage: float
    pass_mark_percentage: float
    attempts_used: int
    max_attempts: int | None
    attempts_remaining: int | None

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS

    @property
    def certificate_due(self) -> bool:
        """A certificate is due exactly for a pass. Failing earns nothing to issue."""
        return self.passed

    @property
    def may_reattempt(self) -> bool:
        """Whether the learner can sit the quiz again -- the answer a failed learner needs."""
        return self.attempts_remaining is None or self.attempts_remaining > 0


def gate(
    *,
    percentage: float,
    pass_mark_percentage: float,
    attempts_used: int,
    max_attempts: int | None,
) -> Gate:
    """Decide pass/fail and work out where the learner stands on their attempt allowance."""
    return Gate(
        outcome=decide(percentage, pass_mark_percentage),
        percentage=percentage,
        pass_mark_percentage=pass_mark_percentage,
        attempts_used=max(0, attempts_used),
        max_attempts=max_attempts,
        attempts_remaining=attempts_remaining(max_attempts, attempts_used),
    )
