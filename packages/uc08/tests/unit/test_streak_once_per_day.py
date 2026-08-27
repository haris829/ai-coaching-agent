"""The once-per-day rule, idempotency, cross-device binding, longest streak."""

from __future__ import annotations

from datetime import timedelta

from uc08.adapters.mock.scenarios import multiple_interactions_same_day
from uc08.application.session import ResolvedSession
from uc08.domain.enums import SessionIdSource, StreakOutcome
from tests.conftest import USER, build_harness


def test_twelve_questions_in_one_afternoon_is_one_day(clock):
    harness = build_harness(clock)

    first = harness.record("q-1")
    assert first.outcome is StreakOutcome.STARTED
    assert first.streak.current_streak_days == 1

    for index in range(2, 13):
        clock.advance(minutes=20)
        result = harness.record(f"q-{index}")
        assert result.outcome is StreakOutcome.UNCHANGED_SAME_DAY
        assert result.streak.current_streak_days == 1

    assert harness.streaks.get(USER).current_streak_days == 1


def test_same_day_scenario_from_the_mock_matrix_yields_one_day(clock):
    """The scope-named ``multiple interactions same day`` scenario."""
    provider = multiple_interactions_same_day(clock)
    read = provider.interactions_in_window(
        "conformance-user", clock.now() - timedelta(hours=24)
    )
    assert len(read.interactions) == 12
    assert {item.occurred_at.date() for item in read.interactions} == {clock.now().date()}


def test_replaying_the_same_interaction_does_not_double_increment(clock):
    harness = build_harness(clock)
    harness.record("day-1")
    clock.advance(hours=23)
    incremented = harness.record("day-2")
    assert incremented.streak.current_streak_days == 2

    replay = harness.streak_service.record_activity(
        user_id=USER,
        interaction_id="day-2",
        session=ResolvedSession("sess-replay", SessionIdSource.RECEIVED),
    )

    assert replay.idempotent_replay is True
    assert replay.outcome is StreakOutcome.IDEMPOTENT_REPLAY
    assert replay.streak.current_streak_days == 2
    assert harness.streaks.get(USER).current_streak_days == 2


def test_replay_after_a_day_boundary_still_changes_nothing(clock):
    harness = build_harness(clock)
    harness.record("day-1")
    clock.advance(hours=23)

    for _ in range(3):
        replay = harness.streak_service.record_activity(
            user_id=USER,
            interaction_id="day-1",
            session=ResolvedSession("sess-1", SessionIdSource.RECEIVED),
        )
        assert replay.idempotent_replay is True
        assert replay.streak.current_streak_days == 1


def test_streak_is_bound_to_the_account_across_three_devices(clock):
    """Three sequential logins from three simulated devices, one streak."""
    harness = build_harness(clock)
    devices = [
        ("device-phone", "sess-phone"),
        ("device-laptop", "sess-laptop"),
        ("device-tablet", "sess-tablet"),
    ]

    counts = []
    for index, (device, session_id) in enumerate(devices):
        if index:
            clock.advance(hours=23)
        harness.ledger.add_interaction(USER, clock.now(), f"{device}-interaction", topic="conduct")
        result = harness.streak_service.record_activity(
            user_id=USER,
            interaction_id=f"{device}-interaction",
            session=ResolvedSession(session_id, SessionIdSource.RECEIVED),
        )
        counts.append(result.streak.current_streak_days)

    assert counts == [1, 2, 3]
    assert harness.streaks.get(USER).current_streak_days == 3
    # One record for the account, not one per device or session.
    assert harness.streak_service.get_state(USER).streak.current_streak_days == 3


def test_three_devices_on_the_same_day_still_count_one_day(clock):
    harness = build_harness(clock)
    for device in ("phone", "laptop", "tablet"):
        clock.advance(minutes=30)
        harness.ledger.add_interaction(USER, clock.now(), f"{device}-i", topic="conduct")
        harness.streak_service.record_activity(
            user_id=USER,
            interaction_id=f"{device}-i",
            session=ResolvedSession(f"sess-{device}", SessionIdSource.RECEIVED),
        )
    assert harness.streaks.get(USER).current_streak_days == 1


def test_longest_streak_is_preserved_across_a_reset(clock):
    harness = build_harness(clock)
    harness.record("d1")
    for day in range(2, 9):
        clock.advance(hours=23)
        harness.record(f"d{day}")
    assert harness.streaks.get(USER).current_streak_days == 8
    assert harness.streaks.get(USER).longest_streak_days == 8

    clock.advance(days=3)
    after_gap = harness.record("back-again")
    assert after_gap.outcome is StreakOutcome.RESET
    assert after_gap.streak.current_streak_days == 1
    assert after_gap.streak.longest_streak_days == 8

    # And a shorter new run does not lower the record.
    clock.advance(hours=23)
    harness.record("next-day")
    assert harness.streaks.get(USER).current_streak_days == 2
    assert harness.streaks.get(USER).longest_streak_days == 8


def test_two_accounts_do_not_share_a_streak(clock):
    harness = build_harness(clock)
    harness.record("a-1", user_id="learner-a")
    clock.advance(hours=23)
    harness.record("a-2", user_id="learner-a")
    harness.record("b-1", user_id="learner-b")

    assert harness.streaks.get("learner-a").current_streak_days == 2
    assert harness.streaks.get("learner-b").current_streak_days == 1
