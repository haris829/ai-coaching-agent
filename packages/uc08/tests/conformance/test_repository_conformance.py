"""Repository contract conformance, parameterised on the backend.

Both local persistence implementations must behave identically. A platform store
adapter added later joins this suite by appending one entry to ``BACKENDS``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from uc08.adapters.persistence import jsonfile, memory
from uc08.domain.enums import DeliveryStatus, FreezeOfferStatus, SourceStatus
from uc08.domain.models import Badge, FreezeOffer, StreakRecord, WeeklySummary

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
USER = "learner-1"


def _memory_backend(_tmp_path):
    return {
        "streaks": memory.InMemoryStreakRepository(),
        "badges": memory.InMemoryBadgeRepository(),
        "summaries": memory.InMemoryWeeklySummaryRepository(),
        "offers": memory.InMemoryFreezeOfferRepository(),
        "processed": memory.InMemoryProcessedInteractionStore(),
    }


def _jsonfile_backend(tmp_path):
    directory = tmp_path / "uc08-data"
    return {
        "streaks": jsonfile.JsonFileStreakRepository(directory),
        "badges": jsonfile.JsonFileBadgeRepository(directory),
        "summaries": jsonfile.JsonFileWeeklySummaryRepository(directory),
        "offers": jsonfile.JsonFileFreezeOfferRepository(directory),
        "processed": jsonfile.JsonFileProcessedInteractionStore(directory),
    }


BACKENDS = [("memory", _memory_backend), ("jsonfile", _jsonfile_backend)]


@pytest.fixture(params=BACKENDS, ids=[name for name, _ in BACKENDS])
def repositories(request, tmp_path):
    _name, builder = request.param
    return builder(tmp_path)


def _streak(days: int) -> StreakRecord:
    return StreakRecord(
        user_id=USER,
        current_streak_days=days,
        longest_streak_days=days,
        last_activity_at=NOW,
        streak_started_at=NOW - timedelta(days=days - 1),
        freeze_available=True,
        freeze_used_at=None,
        updated_at=NOW,
    )


def test_a_missing_streak_reads_as_none(repositories):
    assert repositories["streaks"].get("nobody") is None


def test_a_streak_round_trips_exactly(repositories):
    record = _streak(5)
    repositories["streaks"].save(record)
    assert repositories["streaks"].get(USER) == record


def test_saving_a_streak_replaces_rather_than_appends(repositories):
    repositories["streaks"].save(_streak(5))
    repositories["streaks"].save(_streak(6))
    assert repositories["streaks"].get(USER).current_streak_days == 6


def test_badges_are_idempotent_on_user_and_milestone(repositories):
    first = Badge(badge_id="badge-10-x", user_id=USER, milestone=10, awarded_at=NOW, question_count_at_award=10)
    duplicate = Badge(
        badge_id="badge-10-x",
        user_id=USER,
        milestone=10,
        awarded_at=NOW + timedelta(days=1),
        question_count_at_award=99,
    )
    repositories["badges"].award(first)
    repositories["badges"].award(duplicate)

    held = repositories["badges"].get_all(USER)
    assert len(held) == 1
    # The original award stands; a repeat does not rewrite history.
    assert held[0] == first


def test_badges_are_returned_in_milestone_order(repositories):
    for milestone in (100, 10, 50):
        repositories["badges"].award(
            Badge(
                badge_id=f"badge-{milestone}-x",
                user_id=USER,
                milestone=milestone,
                awarded_at=NOW,
                question_count_at_award=milestone,
            )
        )
    assert [badge.milestone for badge in repositories["badges"].get_all(USER)] == [10, 50, 100]


def test_badges_are_scoped_to_the_account(repositories):
    repositories["badges"].award(
        Badge(badge_id="b", user_id=USER, milestone=10, awarded_at=NOW, question_count_at_award=10)
    )
    assert repositories["badges"].get_all("someone-else") == ()


def _summary(week: str, start: datetime) -> WeeklySummary:
    return WeeklySummary(
        summary_id=f"ws-{USER}-{week}",
        user_id=USER,
        week=week,
        week_start_at=start,
        week_end_at=start + timedelta(days=7),
        generated_at=start + timedelta(days=7),
        topics_covered=("conduct",),
        topics_status=SourceStatus.AVAILABLE,
        questions_asked=3,
        questions_asked_status=SourceStatus.AVAILABLE,
        current_streak_days=4,
        suggested_topic=None,
        suggested_topic_status=SourceStatus.EMPTY,
        delivery_status=DeliveryStatus.PENDING,
    )


def test_summaries_round_trip_and_are_keyed_by_week(repositories):
    summary = _summary("2026-W11", datetime(2026, 3, 9, tzinfo=timezone.utc))
    repositories["summaries"].save(summary)
    assert repositories["summaries"].get(USER, "2026-W11") == summary
    assert repositories["summaries"].get(USER, "2026-W12") is None


def test_summaries_are_listed_most_recent_first(repositories):
    repositories["summaries"].save(_summary("2026-W11", datetime(2026, 3, 9, tzinfo=timezone.utc)))
    repositories["summaries"].save(_summary("2026-W12", datetime(2026, 3, 16, tzinfo=timezone.utc)))
    assert [item.week for item in repositories["summaries"].list_for_user(USER)] == ["2026-W12", "2026-W11"]
    assert repositories["summaries"].list_for_user("nobody") == ()


def test_freeze_offers_round_trip_and_the_latest_wins(repositories):
    first = FreezeOffer(
        offer_id="fo-1",
        user_id=USER,
        status=FreezeOfferStatus.OFFERED,
        offered_at=NOW,
        expires_at=NOW + timedelta(hours=24),
        preserved_streak_days=7,
        preserved_streak_started_at=NOW - timedelta(days=7),
    )
    repositories["offers"].save(first)
    assert repositories["offers"].get_latest(USER) == first

    answered = first.model_copy(update={"status": FreezeOfferStatus.ACCEPTED, "answered_at": NOW})
    repositories["offers"].save(answered)
    assert repositories["offers"].get_latest(USER).status is FreezeOfferStatus.ACCEPTED

    second = first.model_copy(update={"offer_id": "fo-2", "offered_at": NOW + timedelta(days=40)})
    repositories["offers"].save(second)
    assert repositories["offers"].get_latest(USER).offer_id == "fo-2"
    assert repositories["offers"].get_latest("nobody") is None


def test_processed_interactions_are_remembered_per_account(repositories):
    store = repositories["processed"]
    assert store.was_processed(USER, "i-1") is False
    store.mark_processed(USER, "i-1")
    store.mark_processed(USER, "i-1")
    assert store.was_processed(USER, "i-1") is True
    assert store.was_processed("someone-else", "i-1") is False
    assert store.was_processed(USER, "i-2") is False


def test_no_repository_offers_a_delete():
    for module in (memory, jsonfile):
        for name in dir(module):
            member = getattr(module, name)
            if isinstance(member, type):
                for attribute in dir(member):
                    assert not attribute.startswith(("delete", "remove", "revoke", "purge")), (
                        name,
                        attribute,
                    )
