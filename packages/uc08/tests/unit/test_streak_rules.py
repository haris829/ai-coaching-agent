"""The rules module in isolation: pure functions, no clock, no I/O."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from uc08.domain import streak_rules
from uc08.domain.enums import FreezeOfferStatus, StreakOutcome
from uc08.domain.models import FreezeOffer, StreakRecord

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
USER = "learner-1"


def _record(**overrides) -> StreakRecord:
    base = dict(
        user_id=USER,
        current_streak_days=4,
        longest_streak_days=9,
        last_activity_at=NOW - timedelta(hours=23),
        streak_started_at=NOW - timedelta(days=4),
        freeze_available=True,
        freeze_used_at=None,
        updated_at=NOW - timedelta(hours=23),
    )
    base.update(overrides)
    return StreakRecord(**base)


def _decide(streak, *, prior: int, now: datetime = NOW, window: int = 24):
    return streak_rules.decide(
        user_id=USER, streak=streak, now=now, prior_interactions_in_window=prior, window_hours=window
    )


def test_no_record_starts_a_streak():
    decision = _decide(None, prior=0)
    assert decision.outcome is StreakOutcome.STARTED
    assert decision.inactivity_evidence is None


def test_same_utc_day_is_unchanged_regardless_of_prior_activity():
    same_day = _record(last_activity_at=NOW - timedelta(hours=2))
    for prior in (0, 1, 12):
        decision = _decide(same_day, prior=prior)
        assert decision.outcome is StreakOutcome.UNCHANGED_SAME_DAY
        assert decision.inactivity_evidence is None


def test_prior_activity_in_the_window_increments():
    decision = _decide(_record(), prior=1)
    assert decision.outcome is StreakOutcome.INCREMENTED
    assert decision.inactivity_evidence is None


def test_no_prior_activity_in_the_window_resets_with_evidence():
    decision = _decide(_record(last_activity_at=NOW - timedelta(days=3)), prior=0)
    assert decision.outcome is StreakOutcome.RESET
    evidence = decision.inactivity_evidence
    assert evidence is not None
    assert evidence.prior_interactions_in_window == 0
    assert evidence.window_hours == 24
    assert evidence.window_start == NOW - timedelta(hours=24)
    assert evidence.user_id == USER


def test_evidence_is_only_produced_on_a_reset():
    for streak, prior in [(None, 0), (_record(), 1), (_record(last_activity_at=NOW), 0)]:
        assert _decide(streak, prior=prior).inactivity_evidence is None


def test_the_window_start_follows_the_configured_hours():
    assert streak_rules.window_start_for(NOW, 48) == NOW - timedelta(hours=48)
    decision = _decide(_record(last_activity_at=NOW - timedelta(days=2)), prior=0, window=72)
    assert decision.window_start == NOW - timedelta(hours=72)


def test_apply_start_baselines_at_one():
    record = streak_rules.apply_start(user_id=USER, now=NOW)
    assert record.current_streak_days == 1
    assert record.longest_streak_days == 1
    assert record.streak_started_at == NOW
    assert record.freeze_available is True
    assert record.freeze_used_at is None


def test_apply_increment_raises_the_high_water_mark_only_when_exceeded():
    below = streak_rules.apply_increment(_record(current_streak_days=4, longest_streak_days=9), NOW)
    assert below.current_streak_days == 5
    assert below.longest_streak_days == 9

    above = streak_rules.apply_increment(_record(current_streak_days=9, longest_streak_days=9), NOW)
    assert above.current_streak_days == 10
    assert above.longest_streak_days == 10


def test_apply_same_day_changes_no_count():
    before = _record(last_activity_at=NOW - timedelta(hours=2))
    after = streak_rules.apply_same_day(before, NOW)
    assert after.current_streak_days == before.current_streak_days
    assert after.longest_streak_days == before.longest_streak_days
    assert after.last_activity_at == NOW
    assert after.updated_at == NOW


def test_apply_reset_requires_matching_evidence():
    evidence = streak_rules.InactivityEvidence(
        user_id="someone-else",
        evaluated_at=NOW,
        window_start=NOW - timedelta(hours=24),
        window_hours=24,
        prior_interactions_in_window=0,
        last_counted_activity_at=None,
    )
    with pytest.raises(ValueError):
        streak_rules.apply_reset(_record(), NOW, evidence)


def test_apply_reset_carries_the_achievement_forward():
    evidence = _decide(_record(current_streak_days=11, longest_streak_days=11), prior=0).inactivity_evidence
    after = streak_rules.apply_reset(_record(current_streak_days=11, longest_streak_days=11), NOW, evidence)
    assert after.current_streak_days == 1
    assert after.longest_streak_days == 11
    assert after.streak_started_at == NOW


def test_evidence_requires_a_positive_window():
    with pytest.raises(ValueError):
        streak_rules.InactivityEvidence(
            user_id=USER,
            evaluated_at=NOW,
            window_start=NOW,
            window_hours=0,
            prior_interactions_in_window=0,
            last_counted_activity_at=None,
        )


def test_freeze_availability_is_a_utc_calendar_month_question():
    assert streak_rules.freeze_available_at(NOW, None) is True
    assert streak_rules.freeze_available_at(NOW, datetime(2026, 3, 1, tzinfo=timezone.utc)) is False
    assert streak_rules.freeze_available_at(NOW, datetime(2026, 2, 28, 23, 59, tzinfo=timezone.utc)) is True


def test_eligibility_needs_both_a_long_enough_streak_and_an_unused_allowance():
    assert streak_rules.eligible_for_freeze_offer(_record(current_streak_days=7), now=NOW, min_streak_days=7)
    assert not streak_rules.eligible_for_freeze_offer(
        _record(current_streak_days=6), now=NOW, min_streak_days=7
    )
    assert not streak_rules.eligible_for_freeze_offer(
        _record(current_streak_days=9, freeze_used_at=datetime(2026, 3, 2, tzinfo=timezone.utc)),
        now=NOW,
        min_streak_days=7,
    )


def test_freeze_acceptance_adds_the_days_since_the_reset():
    offer = FreezeOffer(
        offer_id="fo-1",
        user_id=USER,
        status=FreezeOfferStatus.OFFERED,
        offered_at=NOW,
        expires_at=NOW + timedelta(hours=24),
        preserved_streak_days=7,
        preserved_streak_started_at=NOW - timedelta(days=7),
    )
    restored = streak_rules.apply_freeze_acceptance(_record(current_streak_days=1, longest_streak_days=7), offer, NOW)
    assert restored.current_streak_days == 8
    assert restored.longest_streak_days == 8
    assert restored.streak_started_at == offer.preserved_streak_started_at
    assert restored.freeze_used_at == NOW
    assert restored.freeze_available is False

    later = streak_rules.apply_freeze_acceptance(
        _record(current_streak_days=2, longest_streak_days=7), offer, NOW + timedelta(hours=20)
    )
    assert later.current_streak_days == 9


def test_the_rules_module_has_no_io_and_no_clock():
    import ast
    import pathlib

    source = (pathlib.Path(streak_rules.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not {module for module in imported if module.startswith("uc08.ports")}
    assert not {module for module in imported if module.startswith("uc08.adapters")}
    assert "logging" not in imported
    assert "datetime.now" not in source
