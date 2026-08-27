"""NARIC behaviour end to end through the assembly service."""

from __future__ import annotations

import pytest

from uc02.domain.models.context import DEFAULT_NARIC_LEVEL
from uc02.domain.models.enums import LevelSource, SourceName, SourceStatus
from uc02.infrastructure.providers.mocks import NaricScenario
from tests.fixtures.factories import make_harness, make_identity


async def _context(scenario: NaricScenario):
    harness = make_harness(naric=scenario)
    outcome = await harness.service.initialize(make_identity())
    return outcome.context


@pytest.mark.parametrize(
    ("scenario", "level", "template"),
    [
        (NaricScenario.LEVEL_3, 3, "basic"),
        (NaricScenario.LEVEL_4, 4, "basic"),
        (NaricScenario.LEVEL_5, 5, "intermediate"),
        (NaricScenario.LEVEL_6, 6, "intermediate"),
        (NaricScenario.LEVEL_7, 7, "advanced"),
        (NaricScenario.LEVEL_7_PLUS, 8, "advanced"),
    ],
)
async def test_each_retrieved_level_maps_to_the_expected_template(scenario, level, template):
    context = await _context(scenario)
    assert context.naric.level == level
    assert context.naric.level_source is LevelSource.RETRIEVED
    assert context.explanation_profile.template_id.value == template
    assert context.source_status[SourceName.NARIC].status is SourceStatus.AVAILABLE


async def test_missing_qualification_defaults_to_level_5_and_records_empty():
    context = await _context(NaricScenario.MISSING_QUALIFICATION)
    assert context.naric.level == DEFAULT_NARIC_LEVEL == 5
    assert context.naric.level_source is LevelSource.DEFAULT
    assert context.explanation_profile.template_id.value == "intermediate"
    # NARIC answered; it simply holds nothing. That is `empty`, not `unavailable`.
    outcome = context.source_status[SourceName.NARIC]
    assert outcome.status is SourceStatus.EMPTY
    assert outcome.fallback_applied is True


async def test_unavailable_naric_defaults_the_level_and_records_unavailable():
    context = await _context(NaricScenario.UNAVAILABLE)
    assert context.naric.level == 5
    assert context.naric.level_source is LevelSource.DEFAULT
    outcome = context.source_status[SourceName.NARIC]
    assert outcome.status is SourceStatus.UNAVAILABLE
    assert outcome.error_category.value == "unavailable"
    assert outcome.fallback_applied is True


async def test_invalid_naric_response_is_recorded_as_invalid_not_unavailable():
    context = await _context(NaricScenario.INVALID_RESPONSE)
    outcome = context.source_status[SourceName.NARIC]
    assert outcome.status is SourceStatus.INVALID
    assert outcome.error_category.value == "invalid_response"
    assert context.naric.level_source is LevelSource.DEFAULT


async def test_raw_level_label_is_carried_through_when_present():
    context = await _context(NaricScenario.LEVEL_7)
    assert context.naric.raw_level_label is not None
    assert "7" in context.naric.raw_level_label


async def test_naric_failure_alone_does_not_block_the_rest_of_the_context():
    context = await _context(NaricScenario.UNAVAILABLE)
    assert context.courses.enrolments
    assert context.legal_profile.speciality_areas
    assert context.question_history.count == 20
    assert context.personalization.available is True
