"""Flag integrity: no duplicates, retry on failure, and no flag ever dropped."""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN_HEADERS, FIXED_NOW
from tests.factories import TOPIC, rating_set
from tests.helpers import seed_repository, seed_via_api
from uc10.adapters.memory.repositories import InMemoryFlagWorkQueue
from uc10.adapters.memory.support import StaticThresholdConfigProvider
from uc10.adapters.mock.faults import (
    FailingAdminNotificationSink,
    FailingFlagRepository,
    FailingFlagWorkQueue,
)
from uc10.application.flagging_service import FlaggingService, InvalidStatusTransition
from uc10.application.results import FlagWriteStatus
from uc10.domain.enums import FlagStatus


@pytest.fixture
def flagged_dataset(ratings_repository):
    """Ten ratings on one topic, all thumbs down: comfortably over any threshold."""
    seed_repository(ratings_repository, rating_set(total=10, downs=10, now=FIXED_NOW))
    return ratings_repository


def build(
    *, ratings, flags, notifications, clock, queue=None, threshold=0.30, minimum=10
) -> FlaggingService:
    return FlaggingService(
        ratings=ratings,
        flags=flags,
        work_queue=queue or InMemoryFlagWorkQueue(now_factory=clock.now),
        notifications=notifications,
        config=StaticThresholdConfigProvider(
            down_rate_threshold=threshold, minimum_sample_size=minimum
        ),
        clock=clock,
    )


# ----------------------------------------------------------------- duplicates


def test_re_evaluating_the_same_topic_updates_the_open_flag_instead_of_raising_another(
    flagged_dataset, flag_repository, notifications, clock
):
    flagger = build(
        ratings=flagged_dataset, flags=flag_repository, notifications=notifications, clock=clock
    )
    first = flagger.evaluate_topic(TOPIC)
    second = flagger.evaluate_topic(TOPIC)
    third = flagger.run_cycle()

    assert first.write_status is FlagWriteStatus.CREATED
    assert second.write_status is FlagWriteStatus.UPDATED
    assert second.flag is not None and first.flag is not None
    assert second.flag.flag_id == first.flag.flag_id
    assert len(flag_repository.all_flags()) == 1
    assert len(third.created) == 0
    assert len(notifications.notified) == 1, "the platform team is notified once, on creation"


def test_new_ratings_update_the_counts_on_the_existing_open_flag(
    flagged_dataset, flag_repository, notifications, clock
):
    flagger = build(
        ratings=flagged_dataset, flags=flag_repository, notifications=notifications, clock=clock
    )
    created = flagger.evaluate_topic(TOPIC).flag
    assert created is not None and created.total_ratings == 10
    assert created.updated_at is None

    seed_repository(flagged_dataset, rating_set(total=5, downs=5, now=FIXED_NOW, start_index=100))
    updated = flagger.evaluate_topic(TOPIC).flag

    assert updated is not None
    assert updated.flag_id == created.flag_id
    assert updated.total_ratings == 15
    assert updated.down_ratings == 15
    assert len(updated.flagging_interaction_ids) == 15
    assert updated.updated_at is not None
    assert len(flag_repository.all_flags()) == 1


def test_a_second_flag_is_never_opened_for_the_same_topic_through_the_api(
    client, interactions, container
):
    seed_via_api(client, interactions, total=10, downs=10, topic_tag="wills_and_probate")
    seed_via_api(
        client, interactions, total=4, downs=4, topic_tag="wills_and_probate_extra"
    )  # different topic, separate flag
    container.flagging.run_cycle()
    container.flagging.run_cycle()

    open_flags = client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS).json()["flags"]
    topics = [flag["topic_tag"] for flag in open_flags]
    assert topics.count("wills_and_probate") == 1


# ------------------------------------------------------- retry / never dropped


def test_a_failed_flag_write_is_retried_on_the_next_cycle_and_the_flag_eventually_exists(
    flagged_dataset, flag_repository, notifications, clock
):
    failing = FailingFlagRepository(flag_repository, fail_writes=1)
    flagger = build(
        ratings=flagged_dataset, flags=failing, notifications=notifications, clock=clock
    )

    first = flagger.evaluate_topic(TOPIC)
    assert first.write_status is FlagWriteStatus.DEFERRED
    assert first.flag is None
    assert flag_repository.all_flags() == [], "nothing was written"
    pending = flagger.pending_flag_work()
    assert len(pending) == 1, "the intent to flag was persisted before the write was attempted"
    assert pending[0].attempts == 1
    assert pending[0].last_reason_code == "write_failed"
    assert pending[0].candidate.down_ratings == 10

    report = flagger.run_cycle()

    assert len(report.retried) == 1
    assert len(report.created) == 1
    assert len(flag_repository.all_flags()) == 1, "the flag eventually exists"
    assert flagger.pending_flag_work() == []
    assert len(notifications.notified) == 1


def test_a_flag_survives_many_consecutive_write_failures(
    flagged_dataset, flag_repository, notifications, clock
):
    failing = FailingFlagRepository(flag_repository, fail_writes=5)
    flagger = build(
        ratings=flagged_dataset, flags=failing, notifications=notifications, clock=clock
    )

    flagger.evaluate_topic(TOPIC)
    for _ in range(4):
        report = flagger.run_cycle()
        assert flag_repository.all_flags() == []
        assert len(flagger.pending_flag_work()) == 1, "never dropped, always still pending"
        assert report.pending_after == 1

    final = flagger.run_cycle()
    assert len(final.created) == 1
    assert len(flag_repository.all_flags()) == 1
    assert flagger.pending_flag_work() == []


def test_a_decided_flag_is_written_even_if_the_topic_later_falls_below_the_rule(
    ratings_repository, flag_repository, notifications, clock
):
    """The rule fired. A flag that has been decided is never silently dropped."""
    seed_repository(ratings_repository, rating_set(total=10, downs=10, now=FIXED_NOW))
    failing = FailingFlagRepository(flag_repository, fail_writes=1)
    flagger = build(
        ratings=ratings_repository, flags=failing, notifications=notifications, clock=clock
    )
    assert flagger.evaluate_topic(TOPIC).write_status is FlagWriteStatus.DEFERRED

    # Ninety happy learners arrive before the retry: the topic is now at 10%.
    seed_repository(
        ratings_repository, rating_set(total=90, downs=0, now=FIXED_NOW, start_index=200)
    )
    report = flagger.run_cycle()

    assert len(report.created) == 1
    written = flag_repository.all_flags()[0]
    assert written.down_ratings == 10, "the recorded decision is written, not a recomputed one"
    assert written.total_ratings == 10
    assert flagger.pending_flag_work() == []


def test_a_notification_failure_does_not_lose_a_persisted_flag(
    flagged_dataset, flag_repository, notifications, clock
):
    sink = FailingAdminNotificationSink(notifications, fail_times=1)
    flagger = build(ratings=flagged_dataset, flags=flag_repository, notifications=sink, clock=clock)

    result = flagger.evaluate_topic(TOPIC)

    assert result.write_status is FlagWriteStatus.CREATED
    assert len(flag_repository.all_flags()) == 1
    assert notifications.notified == []
    assert flagger.pending_flag_work() == []


def test_a_failure_to_close_the_intent_neither_duplicates_nor_loses_a_flag(
    flagged_dataset, flag_repository, notifications, clock
):
    queue = FailingFlagWorkQueue(
        InMemoryFlagWorkQueue(now_factory=clock.now), fail_resolves=1
    )
    flagger = build(
        ratings=flagged_dataset,
        flags=flag_repository,
        notifications=notifications,
        clock=clock,
        queue=queue,
    )

    first = flagger.evaluate_topic(TOPIC)
    assert first.write_status is FlagWriteStatus.CREATED
    assert len(flagger.pending_flag_work()) == 1, "bookkeeping failed; the intent is still open"

    report = flagger.run_cycle()

    assert len(flag_repository.all_flags()) == 1, "not duplicated"
    assert len(report.updated) == 1
    assert flagger.pending_flag_work() == []


def test_a_flag_write_failure_never_reaches_the_learners_rating_request(
    make_client, interactions, ratings_repository, flag_repository
):
    """Flagging is a background consequence of a rating, never a condition of one."""
    client = make_client(flag_repository=FailingFlagRepository(flag_repository, fail_writes=99))
    ids = seed_via_api(client, interactions, total=10, downs=10, topic_tag="anti_money_laundering")

    assert len(ids) == 10, "every rating was accepted"
    assert ratings_repository.count() == 10
    assert flag_repository.all_flags() == []


# --------------------------------------------------------- status transitions


@pytest.mark.parametrize("target", ["reviewed", "confirmed", "corrected"])
def test_a_flag_can_be_marked_reviewed_confirmed_or_corrected(
    client, interactions, container, target
):
    seed_via_api(client, interactions, total=10, downs=10, topic_tag=f"topic_{target}")
    flag_id = container.flag_repository.list_open()[0].flag_id

    response = client.patch(
        f"/api/v1/admin/flags/{flag_id}", json={"status": target}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["status"] == target
    assert container.flag_repository.get(flag_id).status is FlagStatus(target)
    assert container.flag_repository.get(flag_id).updated_at is not None
    assert client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS).json()["count"] == 0


def test_status_transitions_follow_the_documented_order(client, interactions, container):
    seed_via_api(client, interactions, total=10, downs=10, topic_tag="trust_accounts")
    flag_id = container.flag_repository.list_open()[0].flag_id

    def patch(status):
        return client.patch(
            f"/api/v1/admin/flags/{flag_id}", json={"status": status}, headers=ADMIN_HEADERS
        )

    assert patch("reviewed").status_code == 200
    assert patch("reviewed").status_code == 200  # idempotent
    assert patch("confirmed").status_code == 200
    backwards = patch("reviewed")
    assert backwards.status_code == 409
    assert backwards.json()["error"]["code"] == "invalid_status_transition"
    assert patch("corrected").status_code == 200
    assert patch("confirmed").status_code == 409


def test_an_unknown_flag_id_is_a_404(client):
    response = client.patch(
        "/api/v1/admin/flags/flg_missing", json={"status": "reviewed"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "flag_not_found"


def test_an_unknown_status_is_refused(client, interactions, container):
    seed_via_api(client, interactions, total=10, downs=10, topic_tag="costs_disputes")
    flag_id = container.flag_repository.list_open()[0].flag_id
    for body in ({"status": "closed"}, {"status": "OPEN"}, {"state": "reviewed"}):
        assert (
            client.patch(
                f"/api/v1/admin/flags/{flag_id}", json=body, headers=ADMIN_HEADERS
            ).status_code
            == 422
        )


def test_the_service_layer_refuses_an_impossible_transition(
    flagged_dataset, flag_repository, notifications, clock
):
    flagger = build(
        ratings=flagged_dataset, flags=flag_repository, notifications=notifications, clock=clock
    )
    flag = flagger.evaluate_topic(TOPIC).flag
    assert flag is not None
    flagger.set_status(flag.flag_id, FlagStatus.CORRECTED)
    with pytest.raises(InvalidStatusTransition):
        flagger.set_status(flag.flag_id, FlagStatus.OPEN)
