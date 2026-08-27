"""The critical rule: a failed write never resets a streak.

Every assertion here is on the *value*, not on the absence of an exception.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from uc08.adapters.persistence.faults import FaultyStreakRepository
from uc08.adapters.persistence.memory import InMemoryStreakRepository
from uc08.application.streak_persistence import MAX_WRITE_ATTEMPTS
from uc08.domain import streak_rules
from uc08.domain.enums import PersistenceOutcome, StreakOutcome
from uc08.domain.models import StreakRecord
from tests.conftest import USER, build_harness


def _seeded_repository(clock, *, current: int, longest: int, hours_ago: int) -> tuple[InMemoryStreakRepository, StreakRecord]:
    inner = InMemoryStreakRepository()
    record = StreakRecord(
        user_id=USER,
        current_streak_days=current,
        longest_streak_days=longest,
        last_activity_at=clock.now() - timedelta(hours=hours_ago),
        streak_started_at=clock.now() - timedelta(days=current),
        freeze_available=True,
        freeze_used_at=None,
        updated_at=clock.now() - timedelta(hours=hours_ago),
    )
    inner.save(record)
    return inner, record


def test_write_failing_once_retries_and_commits(clock):
    inner, seeded = _seeded_repository(clock, current=6, longest=6, hours_ago=23)
    repo = FaultyStreakRepository(inner, fail_writes=1)
    harness = build_harness(clock, streaks=repo)
    harness.ledger.add_interaction(USER, clock.now() - timedelta(hours=23), "yesterday")

    result = harness.record("today")

    assert repo.save_attempts == MAX_WRITE_ATTEMPTS
    assert repo.committed_writes == 1
    assert result.persistence_outcome is PersistenceOutcome.SAVED_ON_RETRY
    assert result.outcome is StreakOutcome.INCREMENTED
    assert result.streak.current_streak_days == 7
    assert inner.get(USER).current_streak_days == 7
    assert seeded.current_streak_days == 6  # the seed object itself is untouched


def test_write_failing_twice_preserves_the_last_known_count(clock):
    inner, seeded = _seeded_repository(clock, current=6, longest=9, hours_ago=23)
    repo = FaultyStreakRepository(inner, fail_writes=2)
    harness = build_harness(clock, streaks=repo)
    harness.ledger.add_interaction(USER, clock.now() - timedelta(hours=23), "yesterday")

    result = harness.record("today")

    # Exactly one retry: two attempts, not three.
    assert repo.save_attempts == MAX_WRITE_ATTEMPTS
    assert repo.committed_writes == 0
    assert result.persistence_outcome is PersistenceOutcome.PRESERVED_LAST_KNOWN

    # The value is unchanged -- not merely "no exception escaped".
    assert result.streak == seeded
    assert result.streak.current_streak_days == 6
    assert result.streak.longest_streak_days == 9
    assert inner.get(USER) == seeded

    # Engineering was alerted, with the preserved count on the incident.
    assert len(harness.alerts.incidents) == 1
    incident = harness.alerts.incidents[0]
    assert incident.user_id == USER
    assert incident.attempts == MAX_WRITE_ATTEMPTS
    assert incident.preserved_streak_days == 6
    assert incident.preserved_longest_streak_days == 9
    assert incident.intended_streak_days == 7
    assert incident.error_type == "RepositoryWriteFailed"


def test_a_failed_write_does_not_reset_even_when_a_reset_was_determined(clock):
    """The sharpest version of the rule.

    Genuine inactivity is determined, so a reset to 1 is legitimately computed.
    The write then fails twice. The learner keeps the count they had: a system
    problem must not be settled at the learner expense, in either direction.
    """
    inner, seeded = _seeded_repository(clock, current=11, longest=11, hours_ago=72)
    repo = FaultyStreakRepository(inner, fail_writes=2)
    harness = build_harness(clock, streaks=repo)
    harness.ledger.add_interaction(USER, clock.now() - timedelta(hours=72), "three-days-ago")

    result = harness.record("back-after-a-gap")

    assert result.outcome is StreakOutcome.RESET  # the determination was genuine
    assert result.persistence_outcome is PersistenceOutcome.PRESERVED_LAST_KNOWN
    assert result.streak.current_streak_days == 11  # and nothing was written
    assert inner.get(USER).current_streak_days == 11


def test_a_failed_write_leaves_the_interaction_replayable(clock):
    inner, _seeded = _seeded_repository(clock, current=3, longest=3, hours_ago=23)
    repo = FaultyStreakRepository(inner, fail_writes=2)
    harness = build_harness(clock, streaks=repo)
    harness.ledger.add_interaction(USER, clock.now() - timedelta(hours=23), "yesterday")

    harness.record("today")
    assert harness.processed.was_processed(USER, "today") is False

    # The same interaction, once the store recovers, still applies.
    working = build_harness(clock, streaks=inner, ledger=harness.ledger)
    retried = working.record_without_upstream_echo("today")
    assert retried.streak.current_streak_days == 4


def test_no_reset_builder_is_reached_on_a_write_failure(clock, monkeypatch):
    """Runtime companion to the architecture test.

    The reset builder is instrumented; the persistence failure path must not
    invoke it.
    """
    calls: list[int] = []
    real_apply_reset = streak_rules.apply_reset

    def spy(streak, now, evidence):
        calls.append(streak.current_streak_days)
        return real_apply_reset(streak, now, evidence)

    monkeypatch.setattr(streak_rules, "apply_reset", spy)

    inner, _seeded = _seeded_repository(clock, current=5, longest=5, hours_ago=23)
    repo = FaultyStreakRepository(inner, fail_writes=2)
    harness = build_harness(clock, streaks=repo)
    harness.ledger.add_interaction(USER, clock.now() - timedelta(hours=23), "yesterday")

    result = harness.record("today")

    assert calls == []  # a continuing streak never touches the reset builder
    assert result.streak.current_streak_days == 5


def test_inactivity_evidence_cannot_be_forged(clock):
    """The reset builder argument is not constructible without real evidence."""
    with pytest.raises(ValueError):
        streak_rules.InactivityEvidence(
            user_id=USER,
            evaluated_at=clock.now(),
            window_start=clock.now() - timedelta(hours=24),
            window_hours=24,
            prior_interactions_in_window=1,  # there *was* activity
            last_counted_activity_at=None,
        )


def test_a_broken_alert_sink_does_not_fail_the_request(clock):
    inner, seeded = _seeded_repository(clock, current=4, longest=4, hours_ago=23)
    repo = FaultyStreakRepository(inner, fail_writes=2)
    harness = build_harness(clock, streaks=repo)
    harness.ledger.add_interaction(USER, clock.now() - timedelta(hours=23), "yesterday")

    def explode(incident):
        raise RuntimeError("pager is down")

    harness.alerts.streak_write_failed = explode  # type: ignore[method-assign]

    result = harness.record("today")

    assert result.persistence_outcome is PersistenceOutcome.PRESERVED_LAST_KNOWN
    assert result.streak == seeded


def test_activity_source_outage_preserves_rather_than_resets(clock):
    """A source outage is a system problem, so the count is preserved."""
    from uc08.adapters.mock.ledger import Fault

    inner, seeded = _seeded_repository(clock, current=8, longest=8, hours_ago=100)
    harness = build_harness(clock, streaks=inner)
    harness.ledger.with_fault(Fault.UNAVAILABLE)

    result = harness.record_without_upstream_echo("today")

    assert result.outcome is StreakOutcome.UNCHANGED_SOURCE_DEGRADED
    assert result.streak.current_streak_days == 8
    assert result.activity_status.value == "unavailable"
    assert inner.get(USER).current_streak_days == 8
