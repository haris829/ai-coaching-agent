"""Section 5.6 -- the five-exchange cap and its reasoning chain."""

from __future__ import annotations

import pytest

from uc05.domain.enums import DialogueState, Resolution, ResponseKind

from .conftest import build_service

UNRESOLVABLE = "I still cannot see which element the rule is pointing at."


async def run_to_cap(harness):
    """Drive a deliberately unresolvable dialogue until something resolves it."""
    turn = await harness.start()
    transcript = [("system", turn.guiding_question, turn.exchanges_used)]
    while turn.resolution is None:
        turn = await harness.say(turn.dialogue_id, UNRESOLVABLE)
        transcript.append(
            (
                "system",
                turn.guiding_question or turn.response_kind.value,
                turn.exchanges_used,
            )
        )
        assert len(transcript) < 20, "the dialogue never terminated"
    return turn, transcript


async def test_the_cap_fires_at_exactly_five_exchanges(harness):
    await harness.enable()
    turn = await harness.start()

    for expected in (2, 3, 4, 5):
        turn = await harness.say(turn.dialogue_id, UNRESOLVABLE)
        assert turn.resolution is None, f"resolved early at exchange {expected}"
        assert turn.exchanges_used == expected

    capped = await harness.say(turn.dialogue_id, UNRESOLVABLE)

    assert capped.resolution is Resolution.CAPPED
    assert capped.response_kind is ResponseKind.CAPPED_ANSWER
    assert capped.state is DialogueState.CAPPED
    assert capped.exchanges_used == 5
    assert capped.exchanges_remaining == 0


async def test_the_cap_delivers_the_answer_and_the_reasoning_chain(harness):
    await harness.enable()
    capped, transcript = await run_to_cap(harness)

    assert len(transcript) == 6, "five guiding turns plus the capped answer"
    assert capped.answer is not None
    assert capped.reasoning_chain is not None
    assert len(capped.reasoning_chain) == 5


async def test_the_chain_reflects_the_questions_actually_asked(harness):
    await harness.enable()
    turn = await harness.start()
    asked = [turn.guiding_question]
    while turn.resolution is None:
        turn = await harness.say(turn.dialogue_id, UNRESOLVABLE)
        if turn.guiding_question and turn.resolution is None:
            asked.append(turn.guiding_question)

    chain_questions = [step.guiding_question for step in turn.reasoning_chain]
    assert chain_questions == asked, "the chain must quote the record, not invent"


async def test_the_chain_records_what_each_question_was_probing(harness):
    await harness.enable()
    capped, _ = await run_to_cap(harness)

    stored = await harness.dialogues.get(capped.dialogue_id)
    for step, exchange in zip(capped.reasoning_chain, stored.exchanges, strict=True):
        assert step.exchange_number == exchange.exchange_number
        assert step.probing == exchange.probing_focus
        assert step.connection_to_answer
        assert step.probing in step.connection_to_answer


async def test_the_chain_records_the_learners_own_responses(harness):
    await harness.enable()
    capped, _ = await run_to_cap(harness)
    assert all(step.learner_response == UNRESOLVABLE for step in capped.reasoning_chain)


async def test_the_chain_is_assembled_without_consulting_a_generator(harness):
    """Generator calls: five guiding questions plus one answer, and no more."""
    await harness.enable()
    capped, _ = await run_to_cap(harness)
    assert capped.reasoning_chain is not None
    assert harness.guiding.calls == 5
    assert harness.answers.calls == 1


async def test_exchanges_used_and_remaining_are_exposed_throughout(harness):
    await harness.enable()
    turn = await harness.start()
    progress = [(turn.exchanges_used, turn.exchanges_remaining)]
    while turn.resolution is None:
        turn = await harness.say(turn.dialogue_id, UNRESOLVABLE)
        progress.append((turn.exchanges_used, turn.exchanges_remaining))

    assert progress == [(1, 4), (2, 3), (3, 2), (4, 1), (5, 0), (5, 0)]
    assert all(used + remaining == 5 for used, remaining in progress)


@pytest.mark.parametrize("cap", [1, 2, 3, 7])
async def test_a_configured_cap_fires_at_that_number(cap):
    harness = build_service(SOCRATIC_EXCHANGE_CAP=cap)
    await harness.enable()
    turn = await harness.start()
    assert turn.exchange_cap == cap

    for _ in range(cap - 1):
        turn = await harness.say(turn.dialogue_id, UNRESOLVABLE)
        assert turn.resolution is None

    capped = await harness.say(turn.dialogue_id, UNRESOLVABLE)
    assert capped.resolution is Resolution.CAPPED
    assert capped.exchanges_used == cap
    assert len(capped.reasoning_chain) == cap


async def test_the_capped_dialogue_is_closed_and_refuses_further_replies(harness):
    from uc05.domain.errors import InvalidTransition

    await harness.enable()
    capped, _ = await run_to_cap(harness)
    stored = await harness.dialogues.get(capped.dialogue_id)
    assert stored.closed_at is not None

    with pytest.raises(InvalidTransition):
        await harness.say(capped.dialogue_id, "one more thought")


async def test_an_exit_offer_does_not_consume_a_capped_exchange(harness):
    """The offer is free; the cap still binds on real exchanges."""
    await harness.enable()
    turn = await harness.start()
    for _ in range(4):
        turn = await harness.say(turn.dialogue_id, UNRESOLVABLE)
    assert turn.exchanges_used == 5

    offered = await harness.say(turn.dialogue_id, "just tell me")
    assert offered.exchanges_used == 5
    assert offered.response_kind is ResponseKind.EXIT_OFFER

    declined = await harness.say(turn.dialogue_id, "no, keep going")
    assert declined.exchanges_used == 5

    capped = await harness.say(turn.dialogue_id, UNRESOLVABLE)
    assert capped.resolution is Resolution.CAPPED
