"""Milestone badges: exact thresholds, no duplicates, no removal, multi-jump."""

from __future__ import annotations

import inspect

import pytest

from uc08.adapters.mock.ledger import Fault
from uc08.application import badge_service as badge_module
from uc08.ports import repositories as repository_module
from tests.conftest import USER, build_harness


def _milestones(result) -> list[int]:
    return [badge.milestone for badge in result.awarded_badges]


@pytest.mark.parametrize("count", [0, 1, 9])
def test_no_badge_at_all_below_the_first_threshold(clock, count):
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, count)

    result = harness.record("i-1")

    assert _milestones(result) == []
    assert harness.badge_service.held(USER) == ()


@pytest.mark.parametrize(("count", "not_yet_earned"), [(9, 10), (49, 50), (99, 100)])
def test_one_short_of_a_threshold_does_not_award_it(clock, count, not_yet_earned):
    """9, 49 and 99 award nothing new at that threshold -- only 10, 50 and 100 do."""
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, count)

    result = harness.record("i-1")

    assert not_yet_earned not in _milestones(result)
    assert not_yet_earned not in [badge.milestone for badge in harness.badge_service.held(USER)]


@pytest.mark.parametrize(
    ("count", "expected"),
    [(10, [10]), (11, [10]), (50, [10, 50]), (99, [10, 50]), (100, [10, 50, 100]), (150, [10, 50, 100])],
)
def test_awards_at_and_above_each_threshold(clock, count, expected):
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, count)

    result = harness.record("i-1")

    assert _milestones(result) == expected
    assert [badge.milestone for badge in harness.badge_service.held(USER)] == expected


def test_awarded_exactly_once_across_repeated_checks(clock):
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, 10)

    first = harness.record("i-1")
    assert _milestones(first) == [10]

    for index in range(2, 6):
        clock.advance(hours=23)
        harness.ledger.set_question_count(USER, 10 + index)
        again = harness.record(f"i-{index}")
        assert _milestones(again) == []

    held = harness.badge_service.held(USER)
    assert [badge.milestone for badge in held] == [10]
    assert len({badge.badge_id for badge in held}) == 1
    # One notification, not five.
    assert len(harness.notifications.badge_events) == 1


def test_a_jump_past_thresholds_awards_every_milestone_crossed(clock):
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, 10)
    first = harness.record("i-1")
    assert _milestones(first) == [10]

    clock.advance(hours=23)
    harness.ledger.set_question_count(USER, 60)
    jumped = harness.record("i-2")

    # 50 is crossed and awarded; 10 is already held and is not re-awarded.
    assert _milestones(jumped) == [50]
    assert [badge.milestone for badge in harness.badge_service.held(USER)] == [10, 50]


def test_a_jump_from_zero_past_every_threshold_awards_all_of_them(clock):
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, 150)

    result = harness.record("i-1")

    assert _milestones(result) == [10, 50, 100]
    assert [event.milestone for event in harness.notifications.badge_events] == [10, 50, 100]
    assert [event.question_count_at_award for event in harness.notifications.badge_events] == [150, 150, 150]


def test_each_award_emits_a_notification_event_for_a_caller_to_render(clock):
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, 50)

    result = harness.record("i-1")

    assert [event.event_type for event in result.badge_events] == ["badge_awarded", "badge_awarded"]
    assert [event.milestone for event in result.badge_events] == [10, 50]
    assert result.badge_events == tuple(harness.notifications.badge_events)
    # The event carries what a renderer needs and nothing about rendering.
    event = result.badge_events[0]
    assert event.user_id == USER
    assert event.badge_id == "badge-10-" + USER
    assert event.awarded_at == clock.now()


def test_a_failing_notification_does_not_lose_the_badge(clock):
    from uc08.domain.errors import NotificationSendFailed

    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, 10)

    def explode(event):
        raise NotificationSendFailed("chat channel down")

    harness.notifications.badge_awarded = explode  # type: ignore[method-assign]

    result = harness.record("i-1")

    assert _milestones(result) == [10]
    assert [badge.milestone for badge in harness.badge_service.held(USER)] == [10]


def test_an_unavailable_question_count_awards_nothing_and_says_so(clock):
    harness = build_harness(clock)
    harness.ledger.with_fault(Fault.UNAVAILABLE)

    result = harness.record_without_upstream_echo("i-1")

    assert result.awarded_badges == ()
    assert result.question_count is None
    assert result.question_count_status.value == "unavailable"


def test_an_empty_question_count_is_not_an_unavailable_one(clock):
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, None)

    result = harness.record("i-1")

    assert result.question_count_status.value == "empty"
    assert result.awarded_badges == ()


def test_no_code_path_removes_a_badge(clock):
    """Structural: the repository port has no removal method, and neither the
    port nor the service module mentions one."""
    port_methods = {
        name for name, _ in inspect.getmembers(repository_module.BadgeRepository, inspect.isfunction)
    }
    assert port_methods == {"get_all", "award"}

    forbidden = ("remove", "delete", "revoke", "expire", "clear", "reset")
    for module in (repository_module, badge_module):
        source = inspect.getsource(module).lower()
        for name, member in inspect.getmembers(module):
            if inspect.isfunction(member) or inspect.isclass(member):
                assert not any(name.lower().startswith(word) for word in forbidden), name
        # Nothing in these modules calls a badge removal.
        for word in forbidden:
            assert f"badges.{word}" not in source
            assert f"badge_repository.{word}" not in source


def test_badges_survive_a_streak_reset(clock):
    harness = build_harness(clock)
    harness.ledger.set_question_count(USER, 100)
    harness.record("i-1")
    assert len(harness.badge_service.held(USER)) == 3

    clock.advance(days=5)
    reset = harness.record("i-2")

    assert reset.streak.current_streak_days == 1
    assert [badge.milestone for badge in harness.badge_service.held(USER)] == [10, 50, 100]


def test_milestones_come_from_configuration(clock):
    from uc08.config import load_settings

    settings = load_settings(BADGE_MILESTONES="3,7")
    harness = build_harness(clock, settings=settings)
    harness.ledger.set_question_count(USER, 7)

    result = harness.record("i-1")

    assert _milestones(result) == [3, 7]
