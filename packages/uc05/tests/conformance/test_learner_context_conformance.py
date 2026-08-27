"""LearnerContextProvider conformance.

Point this at a real adapter by adding a harness to
``LEARNER_CONTEXT_HARNESSES``; nothing here changes.

    python -m pytest tests/conformance/test_learner_context_conformance.py -q
"""

from __future__ import annotations

import pytest

from uc05.domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from uc05.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc05.domain.models import LearnerContext
from uc05.domain.profiles import DEFAULT_NARIC_LEVEL

from .harness import LEARNER_CONTEXT_HARNESSES, ids
from .shared import (
    assert_honours_timeout,
    assert_no_leak,
    assert_raises_category,
    skip_unless,
)

pytestmark = pytest.mark.parametrize(
    "harness", LEARNER_CONTEXT_HARNESSES, ids=ids(LEARNER_CONTEXT_HARNESSES)
)

SESSION = "session-conformance"
USER = "user-conformance"


async def test_returns_the_platform_type(harness):
    context = await harness.happy().get_context(SESSION, USER)
    assert isinstance(context, LearnerContext)


async def test_values_are_normalised_to_the_platform_enums(harness):
    """Whatever the upstream sent, a NARIC level arrives as the platform enum."""
    context = await harness.happy().get_context(SESSION, USER)
    assert isinstance(context.naric_level, NaricLevel)
    assert isinstance(context.naric_level_source, NaricLevelSource)
    assert all(
        isinstance(status, SourceStatus) for status in context.source_status.values()
    )


async def test_the_happy_case_matches_the_declared_expectation(harness):
    context = await harness.happy().get_context(SESSION, USER)
    if "level" in harness.expectations:
        assert context.naric_level.value == harness.expectations["level"]
    if "practice_area" in harness.expectations:
        assert context.practice_area == harness.expectations["practice_area"]


async def test_a_retrieved_level_is_marked_retrieved(harness):
    context = await harness.happy().get_context(SESSION, USER)
    assert context.naric_level_source is NaricLevelSource.RETRIEVED
    assert context.source_status["naric_level"] is SourceStatus.AVAILABLE


async def test_the_explanation_profile_follows_the_platform_mapping(harness):
    context = await harness.happy().get_context(SESSION, USER)
    if context.naric_level in (NaricLevel.LEVEL_5, NaricLevel.LEVEL_6):
        assert context.explanation_profile.value == "intermediate"


async def test_no_upstream_shape_escapes(harness):
    context = await harness.happy().get_context(SESSION, USER)
    assert_no_leak(context.model_dump(), harness)


async def test_unavailable_raises_the_right_category(harness):
    adapter = skip_unless(harness.unavailable, "unavailable")
    await assert_raises_category(
        lambda: adapter.get_context(SESSION, USER), ProviderUnavailable, harness
    )


async def test_timeout_raises_the_right_category(harness):
    adapter = skip_unless(harness.timeout, "timeout")
    await assert_raises_category(
        lambda: adapter.get_context(SESSION, USER), ProviderTimeout, harness
    )


async def test_malformed_raises_the_right_category(harness):
    adapter = skip_unless(harness.malformed, "malformed")
    await assert_raises_category(
        lambda: adapter.get_context(SESSION, USER), ProviderInvalidResponse, harness
    )


async def test_an_unmappable_level_becomes_the_documented_default(harness):
    """Never a widened enum, never a guess: default + source default + invalid."""
    adapter = skip_unless(harness.invalid_value, "invalid_value")
    context = await adapter.get_context(SESSION, USER)

    assert context.naric_level is DEFAULT_NARIC_LEVEL
    assert context.naric_level_source is NaricLevelSource.DEFAULT
    assert context.source_status["naric_level"] is SourceStatus.INVALID


async def test_an_empty_source_is_empty_not_unavailable(harness):
    adapter = skip_unless(harness.empty, "empty")
    context = await adapter.get_context(SESSION, USER)

    assert context.source_status["naric_level"] is SourceStatus.EMPTY
    assert context.source_status["naric_level"] is not SourceStatus.UNAVAILABLE
    assert context.naric_level is DEFAULT_NARIC_LEVEL
    assert context.naric_level_source is NaricLevelSource.DEFAULT


async def test_a_missing_practice_area_is_absent_not_invented(harness):
    adapter = skip_unless(harness.empty, "empty")
    context = await adapter.get_context(SESSION, USER)
    assert context.practice_area is None
    assert context.source_status["practice_area"] is SourceStatus.EMPTY


async def test_a_hanging_adapter_honours_the_caller_budget(harness):
    adapter = skip_unless(harness.slow, "slow")
    await assert_honours_timeout(lambda: adapter.get_context(SESSION, USER))
