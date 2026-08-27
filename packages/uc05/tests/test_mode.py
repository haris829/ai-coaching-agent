"""Section 5.1 -- the mode toggle."""

from __future__ import annotations

import pytest

from uc05.domain.enums import (
    DialogueState,
    ModeSource,
    Resolution,
    ResponseKind,
)
from uc05.domain.errors import AccessDenied, InvalidTransition

from .conftest import OTHER_USER, SESSION, USER, build_service


async def test_mode_defaults_to_off_and_is_marked_as_a_default(harness):
    state = await harness.service.get_mode(SESSION, USER)
    assert state.enabled is False
    assert state.source is ModeSource.DEFAULT


async def test_toggling_on_persists_and_is_marked_persisted(harness):
    await harness.service.set_mode(SESSION, USER, True)
    state = await harness.service.get_mode(SESSION, USER)
    assert state.enabled is True
    assert state.source is ModeSource.PERSISTED
    assert state.updated_at is not None


async def test_mode_survives_a_simulated_page_refresh(harness):
    """A refresh is a new service instance over the same persisted store."""
    from uc05.application.socratic_service import SocraticService

    await harness.service.set_mode(SESSION, USER, True)

    reloaded = SocraticService(
        settings=harness.settings,
        learner_context=harness.context,
        guiding_generator=harness.guiding,
        answer_generator=harness.answers,
        intent_classifier=harness.intents,
        dialogues=harness.dialogues,
        modes=harness.modes,          # the same persisted store
        interactions=harness.interactions,
    )
    state = await reloaded.get_mode(SESSION, USER)
    assert state.enabled is True
    assert state.source is ModeSource.PERSISTED


async def test_mode_is_per_session_not_per_user(harness):
    await harness.service.set_mode("session-one", USER, True)
    other = await harness.service.get_mode("session-two", USER)
    assert other.enabled is False


async def test_question_with_mode_on_gets_a_guiding_question(harness):
    await harness.enable()
    turn = await harness.start()
    assert turn.response_kind is ResponseKind.GUIDING_QUESTION
    assert turn.answer is None
    assert turn.guiding_question


async def test_toggling_off_reverts_the_next_response_to_the_four_part_answer(harness):
    await harness.enable()
    await harness.start()
    await harness.service.set_mode(SESSION, USER, False)

    turn = await harness.start()
    assert turn.response_kind is ResponseKind.DIRECT_ANSWER
    assert turn.mode_enabled is False
    assert turn.guiding_question is None
    assert turn.answer is not None
    assert turn.answer.plain_english_explanation
    assert turn.answer.formal_legal_definition
    assert turn.answer.practical_example
    assert turn.answer.authority_reference


async def test_a_mode_off_answer_carries_no_socratic_resolution(harness):
    """It is not one of the four exits; it never entered Socratic mode."""
    turn = await harness.start()
    assert turn.resolution is None
    assert turn.dialogue_id is None
    assert turn.state is None


async def test_in_flight_dialogue_is_closed_and_recorded_not_dropped(harness):
    await harness.enable()
    turn = await harness.start()

    result = await harness.service.set_mode(SESSION, USER, False)
    assert result.closed_dialogue_ids == [turn.dialogue_id]

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored is not None, "the dialogue must still exist"
    assert stored.state is DialogueState.ABANDONED
    assert stored.resolution is Resolution.ABANDONED
    assert stored.closed_at is not None
    # The guiding sequence is retained, not discarded with the dialogue.
    assert len(stored.exchanges) == 1
    assert stored.exchanges[0].guiding_question


async def test_an_abandoned_dialogue_refuses_further_replies(harness):
    await harness.enable()
    turn = await harness.start()
    await harness.service.set_mode(SESSION, USER, False)

    with pytest.raises(InvalidTransition):
        await harness.say(turn.dialogue_id, "here is my reasoning about the rule")


async def test_toggling_off_does_not_touch_another_sessions_dialogue(harness):
    await harness.service.set_mode("session-one", USER, True)
    await harness.service.set_mode("session-two", USER, True)
    kept = await harness.start(session_id="session-two")

    await harness.service.set_mode("session-one", USER, False)

    stored = await harness.dialogues.get(kept.dialogue_id)
    assert stored.state is DialogueState.AWAITING_LEARNER_RESPONSE


async def test_a_client_cannot_set_another_users_session_mode(harness):
    await harness.service.set_mode(SESSION, USER, True)
    with pytest.raises(AccessDenied):
        await harness.service.set_mode(SESSION, OTHER_USER, False)


async def test_a_client_cannot_read_another_users_session_mode(harness):
    await harness.service.set_mode(SESSION, USER, True)
    with pytest.raises(AccessDenied):
        await harness.service.get_mode(SESSION, OTHER_USER)


async def test_re_enabling_returns_to_socratic_mode(harness):
    await harness.enable()
    await harness.service.set_mode(SESSION, USER, False)
    await harness.service.set_mode(SESSION, USER, True)

    turn = await harness.start()
    assert turn.response_kind is ResponseKind.GUIDING_QUESTION


async def test_mode_state_is_exposed_for_indicator_rendering(harness):
    """UC-05 exposes what an indicator needs; it does not build the indicator."""
    await harness.enable()
    state = await harness.service.get_mode(SESSION, USER)
    assert set(state.model_dump()) == {
        "session_id",
        "enabled",
        "source",
        "updated_at",
        "closed_dialogue_ids",
    }


async def test_mode_off_writes_no_socratic_interaction_record(harness):
    """A-MODE-OFF-LOGGING: the record's ``mode`` is fixed at "socratic"."""
    service = build_service()
    await service.start()
    assert await service.interactions.list_for_session(SESSION) == []
