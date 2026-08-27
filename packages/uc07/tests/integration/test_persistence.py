"""Persistence: gap reports only, scoped by owner."""

from __future__ import annotations

from dataclasses import replace

from tests.conftest import build_harness
from uc07.adapters.mock import get_scenario
from uc07.adapters.mock.interaction_log import MockInteractionPayload
from uc07.adapters.mock.scenarios import LEARNER, sequence_records
from uc07.adapters.persistence import InMemoryGapReportRepository


def test_generated_report_is_persisted_through_the_repository():
    harness = build_harness("struggle_mixed")
    outcome = harness.service.current_report(harness.user_id)
    stored = harness.repository.get_current(harness.user_id)
    assert stored is not None
    assert stored == outcome.report
    assert harness.repository.saved_count(harness.user_id) == 1


def test_nothing_is_persisted_below_the_threshold():
    harness = build_harness("count_9")
    harness.service.current_report(harness.user_id)
    assert harness.repository.get_current(harness.user_id) is None
    assert harness.repository.saved_count(harness.user_id) == 0


def test_reports_are_scoped_by_owner():
    repository = InMemoryGapReportRepository()

    first = build_harness("struggle_mixed", repository=repository)
    first.service.current_report(first.user_id)

    scenario = get_scenario("struggle_mixed")
    second_scenario = replace(
        scenario,
        user_id="learner-777",
        interactions={
            "learner-777": MockInteractionPayload(
                records=sequence_records(12, user_id="learner-777")
            )
        },
        profiles={"learner-777": scenario.profiles[LEARNER]},
    )
    second = build_harness(second_scenario, repository=repository)
    second.service.current_report("learner-777")

    mine = repository.get_current(LEARNER)
    theirs = repository.get_current("learner-777")
    assert mine is not None and theirs is not None
    assert mine.user_id == LEARNER
    assert theirs.user_id == "learner-777"
    assert mine.report_id != theirs.report_id
    assert repository.get_current("learner-nobody") is None


def test_stored_report_keeps_internal_ownership_information():
    harness = build_harness("struggle_mixed")
    harness.service.current_report(harness.user_id)
    stored = harness.repository.get_current(harness.user_id)
    assert stored is not None
    assert stored.user_id == harness.user_id
    assert "user_id" in stored.model_dump()


def test_repository_is_the_only_component_that_records_anything():
    """The mock sources are pure readers: repeated analysis cannot mutate them."""
    harness = build_harness("struggle_mixed")
    before = harness.scenario.interactions[harness.user_id].records
    feedback_before = harness.scenario.feedback.records
    harness.service.current_report(harness.user_id)
    harness.service.current_report(harness.user_id)
    assert harness.scenario.interactions[harness.user_id].records == before
    assert harness.scenario.feedback.records == feedback_before
    assert harness.repository.saved_count(harness.user_id) == 1
