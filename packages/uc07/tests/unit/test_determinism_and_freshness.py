"""Determinism and report freshness."""

from __future__ import annotations

import pytest

from tests.conftest import FIXED_NOW, LATER, build_harness
from uc07.adapters.clock import FixedClock
from uc07.adapters.mock import MockScenario, get_scenario
from uc07.adapters.mock.interaction_log import MockInteractionPayload
from uc07.adapters.mock.scenarios import sequence_feedback, sequence_records
from uc07.adapters.persistence import InMemoryGapReportRepository

DETERMINISM_SCENARIOS = [
    "count_10",
    "count_11",
    "count_50",
    "struggle_mixed",
    "diverse_topics",
    "narrow_topics",
    "feedback_unavailable",
    "profile_partial",
    "courses_unavailable",
    "interactions_partial",
]


@pytest.mark.parametrize("scenario", DETERMINISM_SCENARIOS)
def test_identical_inputs_produce_identical_reports(scenario):
    first = build_harness(scenario).service.current_report("learner-001").report
    second = build_harness(scenario).service.current_report("learner-001").report
    assert first is not None and second is not None
    assert first == second
    assert first.report_id == second.report_id
    assert first.content_fingerprint == second.content_fingerprint
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_report_is_stable_across_repeated_requests_in_one_service():
    harness = build_harness("struggle_mixed")
    first = harness.service.current_report(harness.user_id)
    second = harness.service.current_report(harness.user_id)
    assert first.report == second.report
    assert first.refreshed is True
    assert second.refreshed is False
    # Nothing new was persisted for an unchanged source state.
    assert harness.repository.saved_count(harness.user_id) == 1


def test_provider_record_order_does_not_change_the_report():
    scenario = get_scenario("struggle_mixed")
    payload = scenario.interactions[scenario.user_id]
    reversed_scenario = scenario.with_interactions(
        MockInteractionPayload(
            records=tuple(reversed(payload.records)), status=payload.status
        )
    )
    forward = build_harness(scenario).service.current_report(scenario.user_id).report
    backward = (
        build_harness(reversed_scenario).service.current_report(scenario.user_id).report
    )
    assert forward == backward


def test_different_clocks_do_not_change_report_content_only_generated_at():
    early = build_harness("struggle_mixed", clock=FixedClock(FIXED_NOW)).service
    late = build_harness("struggle_mixed", clock=FixedClock(LATER)).service
    first = early.current_report("learner-001").report
    second = late.current_report("learner-001").report
    assert first is not None and second is not None
    assert first.content_fingerprint == second.content_fingerprint
    assert first.report_id == second.report_id
    assert first.generated_at != second.generated_at


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def _scenario_with_count(count: int) -> MockScenario:
    """A live source whose interaction count we can move between requests."""
    from uc07.adapters.mock.feedback import MockFeedbackPayload

    base = get_scenario("count_10")
    return MockScenario(
        name=f"live_{count}",
        user_id=base.user_id,
        interactions={
            base.user_id: MockInteractionPayload(records=sequence_records(count))
        },
        feedback=MockFeedbackPayload(records=sequence_feedback(count)),
        profiles=base.profiles,
        courses=base.courses,
    )


def test_current_report_reflects_an_eleventh_interaction():
    repository = InMemoryGapReportRepository()

    at_ten = build_harness(
        _scenario_with_count(10), repository=repository, clock=FixedClock(FIXED_NOW)
    )
    ten = at_ten.service.current_report(at_ten.user_id)
    assert ten.report is not None
    assert ten.report.source_interaction_count == 10
    assert ten.refreshed is True

    at_eleven = build_harness(
        _scenario_with_count(11), repository=repository, clock=FixedClock(LATER)
    )
    eleven = at_eleven.service.current_report(at_eleven.user_id)
    assert eleven.report is not None
    assert eleven.report.source_interaction_count == 11
    assert eleven.refreshed is True
    assert eleven.report.report_id != ten.report.report_id
    assert eleven.report.generated_at == LATER

    # The stored current report is the refreshed one, not the stale snapshot.
    stored = repository.get_current(at_eleven.user_id)
    assert stored is not None
    assert stored.source_interaction_count == 11
    assert repository.saved_count(at_eleven.user_id) == 2


def test_threshold_is_re_evaluated_against_current_source_data():
    repository = InMemoryGapReportRepository()

    ten = build_harness(_scenario_with_count(10), repository=repository)
    assert ten.service.current_report(ten.user_id).report is not None

    # Source data shrinks below the threshold: no stale report may be served.
    nine = build_harness(_scenario_with_count(9), repository=repository)
    outcome = nine.service.current_report(nine.user_id)
    assert outcome.report is None
    assert outcome.progress.status.value == "below_threshold"
    assert outcome.progress.interactions_completed == 9
