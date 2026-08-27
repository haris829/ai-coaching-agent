"""The interaction log record and dialogue retention.

Two different stores with two different jobs, and the tests keep them apart:

*   The **interaction log** is the platform's published record.  One per
    exchange, ``rating_state`` pending, correct dialogue and exchange numbers.
*   The **dialogue record** is UC-05's own retention for the improvement
    pipeline: the full guiding sequence and every learner response.
"""

from __future__ import annotations

from itertools import pairwise

from uc05.domain.enums import RatingState, Resolution, ResponseKind

from .conftest import SESSION, USER, build_service

REPLY_TEMPLATE = "Attempt number {n} at the reasoning."


async def test_one_interaction_record_per_exchange():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    for n in range(1, 5):
        turn = await harness.say(turn.dialogue_id, REPLY_TEMPLATE.format(n=n))

    records = await harness.records()
    question_records = [
        record
        for record in records
        if record.response_kind
        in (
            ResponseKind.GUIDING_QUESTION,
            ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION,
        )
    ]
    assert len(question_records) == 5
    assert [record.exchange_number for record in question_records] == [1, 2, 3, 4, 5]


async def test_rating_state_is_pending_and_uc05_never_changes_it():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    turn = await harness.say(turn.dialogue_id, "reasoning")
    await harness.say(turn.dialogue_id, "I genuinely have no idea.")

    records = await harness.records()
    assert records
    assert all(record.rating_state is RatingState.PENDING for record in records)


async def test_every_record_carries_the_fixed_socratic_mode_literal():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "reasoning")

    for record in await harness.records():
        assert record.mode == "socratic"


async def test_records_are_chained_by_follow_up_of():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    turn = await harness.say(turn.dialogue_id, "one")
    await harness.say(turn.dialogue_id, "two")

    records = await harness.records()
    assert records[0].follow_up_of is None
    for earlier, later in pairwise(records):
        assert later.follow_up_of == earlier.interaction_id


async def test_dialogue_and_session_identity_are_correct_on_every_record():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "reasoning")

    for record in await harness.records():
        assert record.dialogue_id == turn.dialogue_id
        assert record.session_id == SESSION
        assert record.user_id == USER


async def test_the_terminal_record_carries_the_resolution():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    turn = await harness.say(turn.dialogue_id, "just tell me")
    await harness.say(turn.dialogue_id, "yes")

    records = await harness.records()
    assert records[-1].response_kind is ResponseKind.DIRECT_ANSWER
    assert records[-1].resolution is Resolution.EXITED_ON_REQUEST
    assert all(record.resolution is None for record in records[:-1])


async def test_the_record_carries_the_topic_tag_and_naric_level():
    harness = build_service(context_scenario="level_7")
    await harness.enable()
    turn = await harness.start()

    record = (await harness.records())[0]
    assert record.dialogue_id == turn.dialogue_id
    assert record.topic_tag == "contract"
    assert record.naric_level.value == "LEVEL_7"
    assert record.response_id
    assert record.interaction_id != record.response_id


async def test_records_are_retrievable_by_id_and_by_session():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()

    record = (await harness.records())[0]
    assert record.interaction_id == turn.interaction_id
    assert (await harness.interactions.get(record.interaction_id)) == record
    assert (await harness.interactions.list_for_session("other")) == []


# --------------------------------------------------------------------------
# Dialogue retention for the improvement pipeline
# --------------------------------------------------------------------------


async def test_the_full_guiding_sequence_is_retained():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    asked = [turn.guiding_question]
    for n in range(1, 5):
        turn = await harness.say(turn.dialogue_id, REPLY_TEMPLATE.format(n=n))
        asked.append(turn.guiding_question)
    assert len(asked) == 5

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert [exchange.guiding_question for exchange in stored.exchanges] == asked
    assert all(exchange.probing_focus for exchange in stored.exchanges)
    assert all(exchange.question_fingerprint for exchange in stored.exchanges)


async def test_every_learner_response_is_retained():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    said = []
    for n in range(1, 5):
        message = REPLY_TEMPLATE.format(n=n)
        said.append(message)
        turn = await harness.say(turn.dialogue_id, message)
    assert turn.exchanges_used == 5

    stored = await harness.dialogues.get(turn.dialogue_id)
    retained = [
        message.text
        for exchange in stored.exchanges
        for message in exchange.learner_messages
    ]
    assert retained == said


async def test_messages_that_do_not_open_an_exchange_are_still_retained():
    """An exit request, a decline and an aside are all part of the record."""
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")
    await harness.say(turn.dialogue_id, "no, keep going")
    await harness.say(turn.dialogue_id, "tell me a joke")

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored.exchanges_used == 1, "none of those opened an exchange"
    texts = [m.text for m in stored.exchanges[0].learner_messages]
    assert texts == ["just tell me", "no, keep going", "tell me a joke"]
    intents = [m.intent.value for m in stored.exchanges[0].learner_messages]
    assert intents == ["direct_answer_request", "exit_declined", "off_topic"]


async def test_the_dialogue_records_its_own_state_and_resolution():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "I give up")

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored.state.value == "exited_for_question"
    assert stored.resolution is Resolution.EXITED_ON_FRUSTRATION
    assert stored.closed_at is not None
    assert stored.prompt_version
    assert stored.last_interaction_id


async def test_dialogues_are_listable_per_session():
    harness = build_service()
    await harness.enable()
    first = await harness.start()
    second = await harness.start(question="What makes a variation binding?")

    stored = await harness.dialogues.for_session(SESSION)
    assert {d.dialogue_id for d in stored} == {first.dialogue_id, second.dialogue_id}
