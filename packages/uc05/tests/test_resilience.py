"""Resilience: failures degrade or refuse, but never corrupt."""

from __future__ import annotations

import time

import pytest

from uc05.domain.enums import (
    DialogueState,
    NaricLevel,
    NaricLevelSource,
    SourceStatus,
)
from uc05.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)

from .conftest import build_service

REPLY = "My reasoning is that the second element is doing the work."


# --------------------------------------------------------------------------
# Learner context: a failure never leaves the learner without a response
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["unavailable", "timeout"])
async def test_context_failure_still_proceeds_at_level_5_marked_default(scenario):
    harness = build_service(context_scenario=scenario)
    await harness.enable()

    turn = await harness.start()

    assert turn.guiding_question, "the dialogue proceeded"
    assert turn.context.naric_level is NaricLevel.LEVEL_5
    assert turn.context.naric_level_source is NaricLevelSource.DEFAULT
    assert turn.context.explanation_profile.value == "intermediate"
    assert turn.context.practice_area is None, "no invented practice area"


@pytest.mark.parametrize("scenario", ["unavailable", "timeout"])
async def test_context_failure_records_the_status(scenario):
    harness = build_service(context_scenario=scenario)
    await harness.enable()
    turn = await harness.start()
    assert turn.context.source_status["naric_level"] is SourceStatus.UNAVAILABLE

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored.source_status["naric_level"] is SourceStatus.UNAVAILABLE


async def test_an_unmappable_level_is_invalid_not_a_level():
    """Default applied, source ``default``, status ``invalid`` -- never a guess."""
    harness = build_service(context_scenario="invalid_level")
    await harness.enable()
    turn = await harness.start()

    assert turn.context.naric_level is NaricLevel.LEVEL_5
    assert turn.context.naric_level_source is NaricLevelSource.DEFAULT
    assert turn.context.source_status["naric_level"] is SourceStatus.INVALID


async def test_empty_and_unavailable_are_not_conflated():
    empty = build_service(context_scenario="empty")
    await empty.enable()
    empty_turn = await empty.start()

    down = build_service(context_scenario="unavailable")
    await down.enable()
    down_turn = await down.start()

    assert empty_turn.context.source_status["naric_level"] is SourceStatus.EMPTY
    assert down_turn.context.source_status["naric_level"] is SourceStatus.UNAVAILABLE
    assert (
        empty_turn.context.source_status["naric_level"]
        is not down_turn.context.source_status["naric_level"]
    )


async def test_an_absent_practice_area_is_absent_not_invented():
    harness = build_service(context_scenario="no_practice_area")
    await harness.enable()
    turn = await harness.start()
    assert turn.context.practice_area is None
    assert turn.context.source_status["practice_area"] is SourceStatus.EMPTY


# --------------------------------------------------------------------------
# Generator failures: refuse cleanly, corrupt nothing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("timeout", ProviderTimeout),
        ("unavailable", ProviderUnavailable),
        ("malformed", ProviderInvalidResponse),
    ],
)
async def test_generator_failures_surface_by_category(scenario, expected):
    harness = build_service(guiding_script=["normal", scenario])
    await harness.enable()
    turn = await harness.start()
    with pytest.raises(expected):
        await harness.say(turn.dialogue_id, REPLY)


@pytest.mark.parametrize("scenario", ["timeout", "unavailable", "malformed"])
async def test_a_failed_reply_does_not_consume_an_exchange(scenario):
    harness = build_service(guiding_script=["normal", scenario])
    await harness.enable()
    turn = await harness.start()
    before = await harness.dialogues.get(turn.dialogue_id)

    with pytest.raises(
        (ProviderTimeout, ProviderUnavailable, ProviderInvalidResponse)
    ):
        await harness.say(turn.dialogue_id, REPLY)

    after = await harness.dialogues.get(turn.dialogue_id)
    assert after.exchanges_used == before.exchanges_used == 1
    assert after.state is DialogueState.AWAITING_LEARNER_RESPONSE
    assert after.resolution is None
    assert after.updated_at == before.updated_at, "nothing was written"


@pytest.mark.parametrize("scenario", ["timeout", "unavailable", "malformed"])
async def test_a_failed_reply_writes_no_interaction_record(scenario):
    harness = build_service(guiding_script=["normal", scenario])
    await harness.enable()
    turn = await harness.start()
    before = len(await harness.records())

    with pytest.raises(
        (ProviderTimeout, ProviderUnavailable, ProviderInvalidResponse)
    ):
        await harness.say(turn.dialogue_id, REPLY)

    assert len(await harness.records()) == before


async def test_a_dialogue_survives_a_failure_and_continues():
    harness = build_service(guiding_script=["normal", "timeout", "normal"])
    await harness.enable()
    turn = await harness.start()

    with pytest.raises(ProviderTimeout):
        await harness.say(turn.dialogue_id, REPLY)

    resumed = await harness.say(turn.dialogue_id, REPLY)
    assert resumed.exchanges_used == 2
    assert resumed.guiding_question


async def test_a_hanging_generator_is_cancelled_inside_the_budget():
    """``GENERATION_TIMEOUT_MS`` is enforced, not merely configured."""
    harness = build_service(guiding_scenario="slow", GENERATION_TIMEOUT_MS=50)
    await harness.enable()

    started = time.perf_counter()
    with pytest.raises(ProviderTimeout):
        await harness.start()
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 5000, f"waited {elapsed_ms:.0f}ms on a 50ms budget"


async def test_a_timeout_leaves_no_orphan_dialogue():
    harness = build_service(guiding_scenario="timeout")
    await harness.enable()
    with pytest.raises(ProviderTimeout):
        await harness.start()
    assert await harness.dialogues.for_session("session-abc") == []


@pytest.mark.parametrize("scenario", ["missing_part", "malformed"])
async def test_an_incomplete_four_part_answer_is_rejected(scenario):
    harness = build_service(answer_scenario=scenario)
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")

    with pytest.raises(ProviderInvalidResponse):
        await harness.say(turn.dialogue_id, "yes")


async def test_a_failed_answer_leaves_the_dialogue_open_for_a_retry():
    harness = build_service(answer_script=["missing_part", "well_formed"])
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")

    with pytest.raises(ProviderInvalidResponse):
        await harness.say(turn.dialogue_id, "yes")

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored.state is DialogueState.AWAITING_EXIT_CONFIRMATION
    assert stored.resolution is None

    retried = await harness.say(turn.dialogue_id, "yes")
    assert retried.answer is not None


@pytest.mark.parametrize("failure", ["timeout", "unavailable"])
async def test_an_intent_classifier_failure_does_not_corrupt_state(failure):
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    harness.intents.failure = failure

    with pytest.raises((ProviderTimeout, ProviderUnavailable)):
        await harness.say(turn.dialogue_id, REPLY)

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored.exchanges_used == 1
    assert stored.exchanges[0].learner_messages == []


async def test_provider_errors_carry_their_retryability():
    assert ProviderTimeout("p").retryable is True
    assert ProviderUnavailable("p").retryable is True
    assert ProviderInvalidResponse("p").retryable is False
