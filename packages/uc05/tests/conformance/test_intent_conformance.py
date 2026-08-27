"""IntentClassifier conformance.

    python -m pytest tests/conformance/test_intent_conformance.py -q
"""

from __future__ import annotations

import pytest

from uc05.domain.enums import IntentKind
from uc05.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc05.domain.models import IntentResult

from .harness import INTENT_HARNESSES, ids
from .shared import (
    assert_honours_timeout,
    assert_no_leak,
    assert_raises_category,
    skip_unless,
)
from .test_generator_conformance import a_dialogue

pytestmark = pytest.mark.parametrize(
    "harness", INTENT_HARNESSES, ids=ids(INTENT_HARNESSES)
)

MESSAGE = "just tell me the answer"

#: The six the brief fixes as the minimum, plus the two UC-05 adds.  A
#: classifier may not return anything outside this vocabulary.
REQUIRED_MINIMUM = {
    IntentKind.SUBSTANTIVE_RESPONSE,
    IntentKind.DIRECT_ANSWER_REQUEST,
    IntentKind.EXIT_CONFIRMATION,
    IntentKind.EXIT_DECLINED,
    IntentKind.EXPLICIT_FRUSTRATION,
    IntentKind.OFF_TOPIC,
}


async def test_returns_the_platform_type(harness):
    result = await harness.happy().classify(MESSAGE, a_dialogue())
    assert isinstance(result, IntentResult)


async def test_the_kind_is_a_platform_enum_member(harness):
    result = await harness.happy().classify(MESSAGE, a_dialogue())
    assert isinstance(result.kind, IntentKind)


async def test_the_vocabulary_covers_the_specified_minimum(harness):
    assert set(IntentKind) >= REQUIRED_MINIMUM


async def test_frustration_and_casual_difficulty_are_separable(harness):
    """The separation is the requirement, not an implementation detail."""
    assert IntentKind.EXPLICIT_FRUSTRATION in IntentKind
    assert IntentKind.CASUAL_DIFFICULTY in IntentKind
    assert IntentKind.EXPLICIT_FRUSTRATION is not IntentKind.CASUAL_DIFFICULTY


async def test_no_upstream_shape_escapes(harness):
    result = await harness.happy().classify(MESSAGE, a_dialogue())
    assert_no_leak(result.model_dump(), harness)


async def test_no_upstream_confidence_score_reaches_the_domain(harness):
    """UC-05's contract has no notion of classifier confidence."""
    result = await harness.happy().classify(MESSAGE, a_dialogue())
    assert "confidence" not in result.model_dump()
    assert "score" not in result.model_dump()


@pytest.mark.parametrize(
    "state,expected",
    [
        ("unavailable", ProviderUnavailable),
        ("timeout", ProviderTimeout),
        ("malformed", ProviderInvalidResponse),
    ],
)
async def test_failure_modes_raise_the_right_category(harness, state, expected):
    adapter = skip_unless(getattr(harness, state), state)
    await assert_raises_category(
        lambda: adapter.classify(MESSAGE, a_dialogue()), expected, harness
    )


async def test_an_unmappable_intent_code_is_invalid_not_a_guess(harness):
    """An adapter never picks the nearest-looking intent for an unknown code."""
    adapter = skip_unless(harness.malformed, "malformed")
    await assert_raises_category(
        lambda: adapter.classify(MESSAGE, a_dialogue()),
        ProviderInvalidResponse,
        harness,
    )


async def test_honours_the_caller_budget(harness):
    adapter = skip_unless(harness.slow, "slow")
    await assert_honours_timeout(lambda: adapter.classify(MESSAGE, a_dialogue()))
