"""UC-05's gating rules, exercised directly against the pure domain.

Two functions, and the boundaries are the whole point: whether the pass mark is inclusive, and what
"attempts remaining" means when there is no maximum or the allowance has already been used up.
"""

from __future__ import annotations

from app.modules.certification.domain.enums import Outcome
from app.modules.certification.domain.gating import attempts_remaining, decide, gate


class TestDecide:
    def test_above_the_pass_mark_passes(self) -> None:
        assert decide(80.0, 70.0) is Outcome.PASS

    def test_exactly_the_pass_mark_passes(self) -> None:
        """Inclusive: a pass mark of 70 means 70% passes."""
        assert decide(70.0, 70.0) is Outcome.PASS

    def test_just_below_the_pass_mark_fails(self) -> None:
        assert decide(69.99, 70.0) is Outcome.FAIL

    def test_zero_fails_a_normal_pass_mark(self) -> None:
        assert decide(0.0, 1.0) is Outcome.FAIL

    def test_a_pass_mark_of_zero_passes_everything(self) -> None:
        """Not reachable through UC-01, which requires 1-100, but the rule must still be total."""
        assert decide(0.0, 0.0) is Outcome.PASS

    def test_a_hundred_percent_pass_mark_needs_everything(self) -> None:
        assert decide(99.99, 100.0) is Outcome.FAIL
        assert decide(100.0, 100.0) is Outcome.PASS


class TestAttemptsRemaining:
    def test_it_subtracts_used_from_the_maximum(self) -> None:
        assert attempts_remaining(3, 1) == 2

    def test_the_last_attempt_leaves_none(self) -> None:
        assert attempts_remaining(3, 3) == 0

    def test_it_never_goes_negative(self) -> None:
        """An allowance lowered after attempts were used answers zero, not a negative number."""
        assert attempts_remaining(2, 5) == 0

    def test_no_maximum_means_unlimited_not_zero(self) -> None:
        assert attempts_remaining(None, 5) is None


class TestGate:
    def test_a_pass_is_owed_a_certificate(self) -> None:
        decision = gate(percentage=75.0, pass_mark_percentage=70.0, attempts_used=1, max_attempts=3)

        assert decision.passed is True
        assert decision.certificate_due is True
        assert decision.attempts_remaining == 2

    def test_a_fail_is_owed_nothing_and_reports_attempts_left(self) -> None:
        decision = gate(percentage=40.0, pass_mark_percentage=70.0, attempts_used=1, max_attempts=3)

        assert decision.passed is False
        assert decision.certificate_due is False
        assert decision.attempts_remaining == 2
        assert decision.may_reattempt is True

    def test_a_fail_on_the_last_attempt_cannot_be_re_sat(self) -> None:
        decision = gate(percentage=40.0, pass_mark_percentage=70.0, attempts_used=3, max_attempts=3)

        assert decision.attempts_remaining == 0
        assert decision.may_reattempt is False

    def test_an_unlimited_allowance_can_always_be_re_sat(self) -> None:
        decision = gate(
            percentage=10.0, pass_mark_percentage=70.0, attempts_used=9, max_attempts=None
        )

        assert decision.attempts_remaining is None
        assert decision.may_reattempt is True
