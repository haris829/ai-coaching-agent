"""Every mock scenario required by scope section 13 exists and is deterministic.

Two properties are asserted for all of them:

* the scenario is triggerable explicitly (no randomness, no wall-clock dependency),
* the mock honours its port contract -- it returns the declared record type or
  raises one of the three declared error types, and nothing else.
"""

from __future__ import annotations

import asyncio

import pytest

from uc02.domain.errors import ProviderInvalidResponse, ProviderUnavailable
from uc02.domain.models.provider_records import (
    CoursesRecord,
    LegalProfileRecord,
    NaricRecord,
)
from uc02.infrastructure.providers.mocks import (
    CoursesScenario,
    HistoryScenario,
    LegalScenario,
    MockCoursesProvider,
    MockLegalFootprintsProvider,
    MockNaricProvider,
    MockQuestionHistoryProvider,
    NaricScenario,
)

TIMEOUT_SCENARIOS = {
    NaricScenario.TIMEOUT,
    CoursesScenario.TIMEOUT,
    LegalScenario.TIMEOUT,
    HistoryScenario.TIMEOUT,
}


def test_naric_covers_every_required_scenario():
    required = {
        "level_3",
        "level_5",
        "level_7",
        "level_7_plus",
        "missing_qualification",
        "unavailable",
        "invalid_response",
        "timeout",
    }
    assert required <= {member.value for member in NaricScenario}


def test_courses_covers_every_required_scenario():
    required = {
        "single_enrolment",
        "multiple_enrolments",
        "empty",
        "partial_missing_lesson",
        "unavailable",
        "timeout",
    }
    assert required <= {member.value for member in CoursesScenario}


def test_legal_covers_every_required_scenario():
    required = {
        "complete",
        "missing_speciality",
        "missing_practice_area",
        "empty",
        "unavailable",
        "timeout",
    }
    assert required <= {member.value for member in LegalScenario}


def test_history_covers_every_required_scenario():
    required = {
        "exactly_20",
        "fewer_than_20",
        "zero",
        "more_than_20_available",
        "unavailable",
        "malformed_record",
        "timeout",
    }
    assert required <= {member.value for member in HistoryScenario}


@pytest.mark.parametrize("scenario", list(NaricScenario))
async def test_every_naric_scenario_honours_the_port_contract(scenario):
    provider = MockNaricProvider(scenario)
    if scenario in TIMEOUT_SCENARIOS:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(provider.get_qualification_level("u"), timeout=0.05)
        return
    try:
        result = await provider.get_qualification_level("u")
    except (ProviderUnavailable, ProviderInvalidResponse):
        return
    assert isinstance(result, NaricRecord)


@pytest.mark.parametrize("scenario", list(CoursesScenario))
async def test_every_courses_scenario_honours_the_port_contract(scenario):
    provider = MockCoursesProvider(scenario)
    if scenario in TIMEOUT_SCENARIOS:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(provider.get_learning_context("u"), timeout=0.05)
        return
    try:
        result = await provider.get_learning_context("u")
    except (ProviderUnavailable, ProviderInvalidResponse):
        return
    assert isinstance(result, CoursesRecord)


@pytest.mark.parametrize("scenario", list(LegalScenario))
async def test_every_legal_scenario_honours_the_port_contract(scenario):
    provider = MockLegalFootprintsProvider(scenario)
    if scenario in TIMEOUT_SCENARIOS:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(provider.get_profile("u"), timeout=0.05)
        return
    try:
        result = await provider.get_profile("u")
    except (ProviderUnavailable, ProviderInvalidResponse):
        return
    assert isinstance(result, LegalProfileRecord)


@pytest.mark.parametrize("scenario", list(HistoryScenario))
async def test_every_history_scenario_honours_the_port_contract(scenario):
    provider = MockQuestionHistoryProvider(scenario)
    if scenario in TIMEOUT_SCENARIOS:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(provider.get_recent_questions("u", 20), timeout=0.05)
        return
    try:
        result = await provider.get_recent_questions("u", 20)
    except ProviderUnavailable:
        return
    assert isinstance(result, list)


async def test_scenarios_can_be_selected_per_user():
    provider = MockNaricProvider(
        NaricScenario.LEVEL_5, overrides={"learner-advanced": NaricScenario.LEVEL_7}
    )
    assert (await provider.get_qualification_level("learner-advanced")).level == 7
    assert (await provider.get_qualification_level("someone-else")).level == 5


async def test_repeated_calls_return_identical_results():
    """No randomness: the same scenario yields the same record every time."""
    provider = MockCoursesProvider(CoursesScenario.MULTIPLE_ENROLMENTS)
    first = await provider.get_learning_context("u")
    second = await provider.get_learning_context("u")
    assert first == second

    history = MockQuestionHistoryProvider(HistoryScenario.EXACTLY_20)
    assert await history.get_recent_questions("u", 20) == await history.get_recent_questions(
        "u", 20
    )


async def test_timeout_scenarios_hang_rather_than_sleep():
    """A hang is cancelled instantly by the caller's timeout, so tests cannot flake."""
    provider = MockLegalFootprintsProvider(LegalScenario.TIMEOUT)
    import time

    started = time.perf_counter()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(provider.get_profile("u"), timeout=0.05)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5  # bounded by the caller's timeout, not by a sleep duration


async def test_every_mock_records_its_calls():
    naric = MockNaricProvider(NaricScenario.LEVEL_5)
    await naric.get_qualification_level("learner-1")
    await naric.get_qualification_level("learner-2")
    assert naric.calls == ["learner-1", "learner-2"]
    assert naric.call_count == 2
    naric.reset_calls()
    assert naric.call_count == 0
