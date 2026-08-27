"""Reusable contract suites for the remaining ports.

Each suite is parameterised on a *factory*, so a real implementation joins by adding one
entry to the list at the top of its section -- no test is rewritten.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories import TOPIC, rating, rating_set
from uc10.adapters.memory.repositories import (
    InMemoryFlagRepository,
    InMemoryFlagWorkQueue,
    InMemoryRatingRepository,
)
from uc10.adapters.memory.support import (
    ManualClock,
    RecordingAdminNotificationSink,
    SettingsThresholdConfigProvider,
    StaticThresholdConfigProvider,
    SystemClock,
)
from uc10.adapters.mock.faults import FailingFlagRepository, FailingRatingRepository
from uc10.domain.enums import FlagStatus, RatingValue
from uc10.domain.flagging import FlagCandidate
from uc10.domain.ids import new_flag_id
from uc10.domain.models import ContentReviewFlag
from uc10.domain.window import Window
from uc10.ports.admin_notification_sink import AdminNotificationSink
from uc10.ports.clock import Clock
from uc10.ports.errors import RecordNotFound
from uc10.ports.flag_repository import FlagRepository
from uc10.ports.flag_work_queue import FlagWorkQueue
from uc10.ports.rating_repository import RatingRepository
from uc10.ports.threshold_config_provider import ThresholdConfigProvider

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
WINDOW = Window.rolling(NOW, 7)


def _flag(topic_tag: str = TOPIC, *, window: Window = WINDOW, status=FlagStatus.OPEN):
    return ContentReviewFlag(
        flag_id=new_flag_id(),
        topic_tag=topic_tag,
        window_start=window.start,
        window_end=window.end,
        total_ratings=10,
        down_ratings=5,
        down_rate=0.5,
        threshold_applied=0.30,
        flagging_interaction_ids=("int_1", "int_2"),
        created_at=NOW,
        status=status,
        minimum_sample_size_applied=10,
    )


def _candidate(topic_tag: str = TOPIC) -> FlagCandidate:
    return FlagCandidate(
        topic_tag=topic_tag,
        window=WINDOW,
        total_ratings=10,
        down_ratings=10,
        down_rate=1.0,
        threshold_applied=0.30,
        minimum_sample_size_applied=10,
        flagging_interaction_ids=("int_1",),
        evaluated_at=NOW,
    )


# ============================================================ RatingRepository

RATING_REPOSITORIES = {
    "in_memory": InMemoryRatingRepository,
    "fault_decorated": lambda: FailingRatingRepository(InMemoryRatingRepository()),
}


@pytest.fixture(params=sorted(RATING_REPOSITORIES), ids=sorted(RATING_REPOSITORIES))
def rating_repo(request):
    return RATING_REPOSITORIES[request.param]()


def test_rating_repository_satisfies_the_port(rating_repo):
    assert isinstance(rating_repo, RatingRepository)


def test_a_saved_rating_can_be_read_back_for_its_interaction(rating_repo):
    record = rating(value=RatingValue.DOWN, rated_at=NOW, interaction_id="int_x")
    assert rating_repo.save(record) == record
    assert rating_repo.for_interaction("int_x") == [record]


def test_reading_an_interaction_with_no_ratings_returns_an_empty_list(rating_repo):
    assert rating_repo.for_interaction("int_never_rated") == []


def test_ratings_are_returned_oldest_first(rating_repo):
    later = rating(value=RatingValue.UP, rated_at=NOW, interaction_id="int_x")
    earlier = rating(
        value=RatingValue.DOWN, rated_at=NOW - timedelta(hours=1), interaction_id="int_x"
    )
    rating_repo.save(later)
    rating_repo.save(earlier)
    assert [r.rating_id for r in rating_repo.for_interaction("int_x")] == [
        earlier.rating_id,
        later.rating_id,
    ]


def test_superseding_retains_the_record_and_marks_it(rating_repo):
    record = rating(value=RatingValue.DOWN, rated_at=NOW, interaction_id="int_x")
    rating_repo.save(record)
    updated = rating_repo.supersede(record.rating_id, "rat_new")
    assert updated.superseded_by == "rat_new"
    assert len(rating_repo.for_interaction("int_x")) == 1, "retained, never deleted"
    assert rating_repo.for_interaction("int_x")[0].superseded_by == "rat_new"


def test_superseding_an_unknown_rating_raises_record_not_found(rating_repo):
    with pytest.raises(RecordNotFound):
        rating_repo.supersede("rat_missing", "rat_new")


def test_the_window_read_returns_current_ratings_across_all_users(rating_repo):
    for record in rating_set(total=5, downs=5, now=NOW):
        rating_repo.save(record)
    found = rating_repo.current_in_window(WINDOW.start, WINDOW.end)
    assert len(found) == 5
    assert len({r.user_id for r in found}) == 5


def test_the_window_read_excludes_superseded_and_out_of_window_ratings(rating_repo):
    inside = rating(value=RatingValue.DOWN, rated_at=NOW, interaction_id="int_in")
    outside = rating(
        value=RatingValue.DOWN, rated_at=NOW - timedelta(days=8), interaction_id="int_out"
    )
    replaced = rating(value=RatingValue.UP, rated_at=NOW, interaction_id="int_old")
    for record in (inside, outside, replaced):
        rating_repo.save(record)
    rating_repo.supersede(replaced.rating_id, "rat_new")

    found = rating_repo.current_in_window(WINDOW.start, WINDOW.end)
    assert [r.rating_id for r in found] == [inside.rating_id]


# ============================================================== FlagRepository

FLAG_REPOSITORIES = {
    "in_memory": InMemoryFlagRepository,
    "fault_decorated": lambda: FailingFlagRepository(InMemoryFlagRepository(), fail_writes=0),
}


@pytest.fixture(params=sorted(FLAG_REPOSITORIES), ids=sorted(FLAG_REPOSITORIES))
def flag_repo(request):
    return FLAG_REPOSITORIES[request.param]()


def test_flag_repository_satisfies_the_port(flag_repo):
    assert isinstance(flag_repo, FlagRepository)


def test_a_saved_flag_is_readable_and_listed_while_open(flag_repo):
    flag = flag_repo.save(_flag())
    assert flag_repo.get(flag.flag_id) == flag
    assert flag_repo.list_open() == [flag]


def test_open_flag_lookup_matches_topic_and_overlapping_window(flag_repo):
    flag = flag_repo.save(_flag())
    assert flag_repo.open_flag_for(TOPIC, WINDOW) == flag
    assert flag_repo.open_flag_for("another_topic", WINDOW) is None
    far_future = Window.rolling(NOW + timedelta(days=30), 7)
    assert flag_repo.open_flag_for(TOPIC, far_future) is None


def test_a_flag_that_is_no_longer_open_is_not_returned_as_an_open_flag(flag_repo):
    flag = flag_repo.save(_flag())
    flag_repo.update(flag.model_copy(update={"status": FlagStatus.REVIEWED}))
    assert flag_repo.open_flag_for(TOPIC, WINDOW) is None
    assert flag_repo.list_open() == []
    assert flag_repo.get(flag.flag_id).status is FlagStatus.REVIEWED


def test_updating_an_unknown_flag_raises_record_not_found(flag_repo):
    with pytest.raises(RecordNotFound):
        flag_repo.update(_flag())


def test_getting_an_unknown_flag_raises_record_not_found(flag_repo):
    with pytest.raises(RecordNotFound):
        flag_repo.get("flg_missing")


# =============================================================== FlagWorkQueue

WORK_QUEUES = {"in_memory": lambda: InMemoryFlagWorkQueue(now_factory=lambda: NOW)}


@pytest.fixture(params=sorted(WORK_QUEUES), ids=sorted(WORK_QUEUES))
def work_queue(request):
    return WORK_QUEUES[request.param]()


def test_work_queue_satisfies_the_port(work_queue):
    assert isinstance(work_queue, FlagWorkQueue)


def test_an_enqueued_intent_is_pending_until_it_is_resolved(work_queue):
    item = work_queue.enqueue(_candidate())
    assert work_queue.pending() == [item]
    assert work_queue.pending_for_topic(TOPIC) == item

    resolved = work_queue.resolve(item.work_id, "flg_1")
    assert resolved.resolved_flag_id == "flg_1"
    assert work_queue.pending() == []
    assert work_queue.pending_for_topic(TOPIC) is None


def test_a_failed_attempt_keeps_the_intent_pending_and_counts_the_attempt(work_queue):
    item = work_queue.enqueue(_candidate())
    failed = work_queue.mark_failed(item.work_id, "write_failed")
    assert failed.attempts == 1
    assert failed.last_reason_code == "write_failed"
    assert work_queue.pending() == [failed]


def test_a_pending_intent_can_be_refreshed_with_newer_counts(work_queue):
    item = work_queue.enqueue(_candidate())
    refreshed = work_queue.update_candidate(
        item.work_id,
        FlagCandidate(
            topic_tag=TOPIC,
            window=WINDOW,
            total_ratings=20,
            down_ratings=12,
            down_rate=0.6,
            threshold_applied=0.30,
            minimum_sample_size_applied=10,
            flagging_interaction_ids=("int_1", "int_2"),
            evaluated_at=NOW,
        ),
    )
    assert refreshed.candidate.total_ratings == 20
    assert work_queue.pending() == [refreshed]


def test_unknown_work_items_raise_record_not_found(work_queue):
    for call in (
        lambda: work_queue.mark_failed("fwk_missing", "write_failed"),
        lambda: work_queue.resolve("fwk_missing", "flg_1"),
        lambda: work_queue.update_candidate("fwk_missing", _candidate()),
    ):
        with pytest.raises(RecordNotFound):
            call()


# =================================================== ThresholdConfigProvider

POLICY_PROVIDERS = {
    "settings": SettingsThresholdConfigProvider,
    "static": lambda: StaticThresholdConfigProvider(
        down_rate_threshold=0.30, minimum_sample_size=10
    ),
}


@pytest.fixture(params=sorted(POLICY_PROVIDERS), ids=sorted(POLICY_PROVIDERS))
def policy_provider(request):
    return POLICY_PROVIDERS[request.param]()


def test_policy_provider_satisfies_the_port(policy_provider):
    assert isinstance(policy_provider, ThresholdConfigProvider)


def test_policy_values_are_within_their_documented_ranges(policy_provider):
    assert 0.0 <= policy_provider.down_rate_threshold() <= 1.0
    assert isinstance(policy_provider.minimum_sample_size(), int)
    assert policy_provider.minimum_sample_size() >= 1
    assert policy_provider.window_days() >= 1
    assert policy_provider.historical_rating_window_hours() >= 1


def test_policy_values_are_stable_within_an_evaluation(policy_provider):
    assert policy_provider.down_rate_threshold() == policy_provider.down_rate_threshold()
    assert policy_provider.minimum_sample_size() == policy_provider.minimum_sample_size()


def test_the_settings_backed_provider_re_reads_configuration(monkeypatch):
    from uc10.config import reset_settings_cache

    provider = SettingsThresholdConfigProvider()
    monkeypatch.setenv("FLAG_DOWN_RATE_THRESHOLD", "0.42")
    reset_settings_cache()
    assert provider.down_rate_threshold() == 0.42


# ============================================================== Clock and sink

CLOCKS = {"system": SystemClock, "manual": lambda: ManualClock(NOW)}


@pytest.fixture(params=sorted(CLOCKS), ids=sorted(CLOCKS))
def clock_impl(request):
    return CLOCKS[request.param]()


def test_clock_satisfies_the_port_and_returns_utc(clock_impl):
    assert isinstance(clock_impl, Clock)
    moment = clock_impl.now()
    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)


def test_clock_time_never_moves_backwards(clock_impl):
    assert clock_impl.now() <= clock_impl.now()


def test_notification_sink_satisfies_the_port_and_accepts_a_flag():
    sink = RecordingAdminNotificationSink()
    assert isinstance(sink, AdminNotificationSink)
    flag = _flag()
    assert sink.flag_created(flag) is None
    assert sink.notified == [flag]
