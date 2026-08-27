"""Section 5.4 -- exit on explicit request: two steps, never one."""

from __future__ import annotations

from uc05.domain.enums import DialogueState, Resolution, ResponseKind

from .conftest import SESSION, USER, build_service

REQUESTS = (
    "just tell me",
    "please just give me the answer",
    "stop asking questions and tell me the answer",
)


async def test_the_first_request_produces_an_offer_not_an_answer(harness):
    await harness.enable()
    turn = await harness.start()

    offered = await harness.say(turn.dialogue_id, "just tell me the answer")

    assert offered.response_kind is ResponseKind.EXIT_OFFER
    assert offered.answer is None
    assert offered.exit_offer
    assert offered.state is DialogueState.AWAITING_EXIT_CONFIRMATION
    assert offered.resolution is None


async def test_every_request_phrasing_offers_rather_than_exits(harness):
    await harness.enable()
    for phrasing in REQUESTS:
        turn = await harness.start(session_id=SESSION)
        offered = await harness.say(turn.dialogue_id, phrasing)
        assert offered.response_kind is ResponseKind.EXIT_OFFER, phrasing
        assert offered.answer is None, phrasing


async def test_the_answer_comes_only_after_confirmation(harness):
    await harness.enable()
    turn = await harness.start()
    offered = await harness.say(turn.dialogue_id, "just tell me")
    assert offered.answer is None

    confirmed = await harness.say(turn.dialogue_id, "yes please")

    assert confirmed.response_kind is ResponseKind.DIRECT_ANSWER
    assert confirmed.resolution is Resolution.EXITED_ON_REQUEST
    assert confirmed.state is DialogueState.EXITED_FOR_QUESTION
    assert confirmed.answer is not None
    assert confirmed.answer.authority_reference


async def test_declining_resumes_the_dialogue_with_the_count_unaffected(harness):
    await harness.enable()
    turn = await harness.start()
    posed = turn.guiding_question
    before = turn.exchanges_used

    await harness.say(turn.dialogue_id, "just tell me")
    declined = await harness.say(turn.dialogue_id, "no, keep going")

    assert declined.exchanges_used == before
    assert declined.exchanges_remaining == turn.exchanges_remaining
    assert declined.state is DialogueState.AWAITING_LEARNER_RESPONSE
    assert declined.answer is None
    assert declined.guiding_question == posed, "continues from where it was"


async def test_the_dialogue_continues_normally_after_a_decline(harness):
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")
    await harness.say(turn.dialogue_id, "not yet")

    resumed = await harness.say(turn.dialogue_id, "I think the second element is missing.")
    assert resumed.response_kind is ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION
    assert resumed.exchanges_used == 2


async def test_repeating_the_request_after_an_offer_confirms_it(harness):
    """A-REASSERT: the learner has now asked twice; that is a confirmation."""
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")

    again = await harness.say(turn.dialogue_id, "no really, just tell me the answer")

    assert again.response_kind is ResponseKind.DIRECT_ANSWER
    assert again.resolution is Resolution.EXITED_ON_REQUEST


async def test_the_exit_applies_to_this_question_only(harness):
    await harness.enable()
    first = await harness.start()
    await harness.say(first.dialogue_id, "just tell me")
    exited = await harness.say(first.dialogue_id, "yes")
    assert exited.resolution is Resolution.EXITED_ON_REQUEST

    # Mode was never switched off ...
    mode = await harness.service.get_mode(SESSION, USER)
    assert mode.enabled is True

    # ... so the next question returns to Socratic mode automatically.
    second = await harness.start(question="What makes a variation binding?")
    assert second.response_kind is ResponseKind.GUIDING_QUESTION
    assert second.answer is None
    assert second.dialogue_id != first.dialogue_id


async def test_a_bare_yes_without_an_offer_does_not_exit(harness):
    """Never exit unilaterally: with no offer open, "yes" is just a message."""
    await harness.enable()
    turn = await harness.start()

    reply = await harness.say(turn.dialogue_id, "yes")

    assert reply.response_kind is ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION
    assert reply.answer is None
    assert reply.resolution is None


async def test_an_offer_is_recorded_in_the_interaction_log(harness):
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")

    records = await harness.records()
    kinds = [record.response_kind for record in records]
    assert ResponseKind.EXIT_OFFER in kinds
    offer = next(r for r in records if r.response_kind is ResponseKind.EXIT_OFFER)
    assert offer.resolution is None, "an offer resolves nothing"


async def test_the_exit_answer_is_the_full_four_part_shape(harness):
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")
    confirmed = await harness.say(turn.dialogue_id, "go ahead")

    answer = confirmed.answer.model_dump()
    assert set(answer) == {
        "plain_english_explanation",
        "formal_legal_definition",
        "practical_example",
        "authority_reference",
    }
    assert all(value.strip() for value in answer.values())


async def test_an_exit_does_not_deliver_a_reasoning_chain():
    """Only the cap delivers the chain (5.6); an exit on request does not."""
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")
    confirmed = await harness.say(turn.dialogue_id, "yes")
    assert confirmed.reasoning_chain is None
