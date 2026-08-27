"""The qualifying-interaction rule and the exact ten-interaction threshold."""

from __future__ import annotations

import pytest

from tests.conftest import DEFAULT_THRESHOLDS, build_harness
from uc07.adapters.mock.scenarios import (
    LEARNER,
    OTHER_LEARNER,
    interaction,
    sequence_records,
)
from uc07.adapters.mock.interaction_log import MockInteractionLogProvider, MockInteractionPayload
from uc07.domain.counting import qualifying_interactions
from uc07.domain.enums import ThresholdStatus


def _records(raw):
    provider = MockInteractionLogProvider({LEARNER: MockInteractionPayload(records=raw)})
    return provider.for_user(LEARNER)


# ---------------------------------------------------------------------------
# Counting rule
# ---------------------------------------------------------------------------


def test_every_valid_record_counts_once():
    result = qualifying_interactions(_records(sequence_records(10)), user_id=LEARNER)
    assert result.count == 10
    assert result.duplicates_discarded == 0
    assert result.other_user_records_discarded == 0


def test_follow_up_interactions_count():
    raw = (
        interaction(interaction_id="a", session_id="s", topic_tag="t", minute=0),
        interaction(
            interaction_id="b", session_id="s", topic_tag="t", minute=1, follow_up_of="a"
        ),
    )
    result = qualifying_interactions(_records(raw), user_id=LEARNER)
    assert result.count == 2
    assert sum(1 for record in result.records if record.is_follow_up) == 1


def test_clarifying_interactions_count_when_represented_as_records():
    raw = (
        interaction(interaction_id="a", session_id="s", topic_tag="t", minute=0),
        interaction(
            interaction_id="b",
            session_id="s",
            topic_tag="t",
            minute=1,
            question_class="clarification",
            follow_up_of="a",
        ),
    )
    assert qualifying_interactions(_records(raw), user_id=LEARNER).count == 2


def test_explain_differently_counter_does_not_add_to_the_count():
    raw = (
        interaction(
            interaction_id="a",
            session_id="s",
            topic_tag="t",
            minute=0,
            explain_differently_count=7,
        ),
    )
    assert qualifying_interactions(_records(raw), user_id=LEARNER).count == 1


def test_duplicate_interaction_ids_count_once():
    harness = build_harness("duplicate_interaction_ids")
    progress = harness.service.progress(harness.user_id)
    assert progress.interactions_completed == 10
    assert progress.status is ThresholdStatus.AVAILABLE


def test_records_belonging_to_another_learner_are_discarded():
    harness = build_harness("mixed_owner_records")
    progress = harness.service.progress(harness.user_id)
    assert progress.interactions_completed == 10

    result = qualifying_interactions(
        _records(
            sequence_records(3)
            + (
                interaction(
                    interaction_id="foreign",
                    session_id="s",
                    topic_tag="t",
                    minute=99,
                    user_id=OTHER_LEARNER,
                ),
            )
        ),
        user_id=LEARNER,
    )
    assert result.count == 3
    assert result.other_user_records_discarded == 1


def test_counting_is_order_independent():
    forward = _records(sequence_records(10))
    reverse = tuple(reversed(forward))
    assert qualifying_interactions(forward, user_id=LEARNER).records == (
        qualifying_interactions(reverse, user_id=LEARNER).records
    )


# ---------------------------------------------------------------------------
# Threshold matrix: 0, 5, 9, 10, 11, 50
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected_status", "expected_remaining"),
    [
        (0, ThresholdStatus.BELOW_THRESHOLD, 10),
        (5, ThresholdStatus.BELOW_THRESHOLD, 5),
        (9, ThresholdStatus.BELOW_THRESHOLD, 1),
        (10, ThresholdStatus.AVAILABLE, 0),
        (11, ThresholdStatus.AVAILABLE, 0),
        (50, ThresholdStatus.AVAILABLE, 0),
    ],
)
def test_progress_across_the_threshold_matrix(count, expected_status, expected_remaining):
    harness = build_harness(f"count_{count}")
    progress = harness.service.progress(harness.user_id)
    assert progress.interactions_completed == count
    assert progress.threshold == DEFAULT_THRESHOLDS.gap_report_threshold == 10
    assert progress.status is expected_status
    assert progress.interactions_remaining == expected_remaining


@pytest.mark.parametrize("count", [0, 5, 9])
def test_no_report_below_ten_interactions(count):
    harness = build_harness(f"count_{count}")
    outcome = harness.service.current_report(harness.user_id)
    assert outcome.report is None
    assert outcome.progress.status is ThresholdStatus.BELOW_THRESHOLD
    assert harness.repository.get_current(harness.user_id) is None


def test_no_report_at_nine_but_report_at_ten():
    nine = build_harness("count_9").service.current_report(LEARNER)
    ten = build_harness("count_10").service.current_report(LEARNER)
    assert nine.report is None
    assert ten.report is not None
    assert ten.report.source_interaction_count == 10


@pytest.mark.parametrize("count", [10, 11, 50])
def test_report_available_at_and_above_ten(count):
    harness = build_harness(f"count_{count}")
    outcome = harness.service.current_report(harness.user_id)
    assert outcome.report is not None
    assert outcome.progress.status is ThresholdStatus.AVAILABLE
    assert outcome.report.source_interaction_count == count
    assert outcome.report.threshold == 10


def test_below_threshold_is_not_an_error_and_reports_progress_fields():
    harness = build_harness("count_9")
    outcome = harness.service.current_report(harness.user_id)
    assert outcome.progress.interactions_completed == 9
    assert outcome.progress.interactions_remaining == 1
    assert outcome.progress.status.value == "below_threshold"


def test_threshold_comes_from_configuration_not_code():
    from uc07.application.config import AnalysisThresholds

    thresholds = AnalysisThresholds(
        gap_report_threshold=5,
        min_topic_areas=3,
        explain_differently_struggle_threshold=2,
        low_rating_struggle_threshold=1,
        follow_up_struggle_threshold=2,
    )
    harness = build_harness("count_5", thresholds=thresholds)
    outcome = harness.service.current_report(harness.user_id)
    assert outcome.report is not None
    assert outcome.report.threshold == 5
