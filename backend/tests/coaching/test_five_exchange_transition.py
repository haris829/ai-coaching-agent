"""The five-exchange transition and direct concept explanation (§15, §16, §33).

    exchange 1 → Socratic
    exchange 2 → Socratic
    exchange 3 → Socratic
    exchange 4 → Socratic
    exchange 5 → the learner may choose a direct explanation

The threshold is what stops "explain it to me" from being an answer button on turn one. These tests
check that it holds, that it is reachable, and that an outage cannot move it.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.modules.coaching.domain.enums import CoachingMode, ExchangeOutcome, MessageRole
from app.modules.coaching.domain.errors import DirectExplanationNotAvailableError
from app.modules.coaching.integration.activity import CoachingActivityType
from tests.coaching.world import Q_MULTI, World, build_world

pytestmark = pytest.mark.anyio


async def test_the_first_four_exchanges_do_not_offer_the_choice(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    for expected in (1, 2, 3, 4):
        exchange = await world.say(session_id, f"My reasoning, part {expected}.")
        assert exchange.state.session.exchange_count == expected
        assert exchange.state.session.mode is CoachingMode.SOCRATIC
        assert exchange.state.session.direct_explanation_available is False


async def test_the_fifth_exchange_makes_the_choice_available(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    await world.exchange_n_times(session_id, 4)
    fifth = await world.say(session_id, "I am still not sure why B was wrong.")

    assert fifth.state.session.exchange_count == 5
    assert fifth.state.session.direct_explanation_available is True
    # Still Socratic until the learner actually chooses otherwise (§15).
    assert fifth.state.session.mode is CoachingMode.SOCRATIC


async def test_the_state_a_frontend_reads_exposes_the_transition(world: World) -> None:
    """§15's ``exchange_count = 5`` / ``direct_explanation_available = true`` (§4)."""
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    await world.exchange_n_times(session_id, 5)
    state = (await world.coaching.get_session(learner_id="learner-1", session_id=session_id))

    payload = state.as_dict()["session"]
    assert payload["exchange_count"] == 5
    assert payload["direct_explanation_available"] is True
    assert payload["exchanges_until_choice"] == 0


async def test_exchanges_until_choice_counts_down(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    exchange = await world.say(session_id, "First thought.")

    assert exchange.state.session.exchanges_until_choice() == 4


async def test_direct_explanation_is_refused_before_the_threshold(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 4)

    with pytest.raises(DirectExplanationNotAvailableError) as error:
        await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)

    assert error.value.status_code == 409
    assert error.value.context["exchangeCount"] == 4
    assert error.value.context["directExplanationThreshold"] == 5


async def test_direct_explanation_is_allowed_after_the_threshold(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 5)

    chosen = await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)

    assert chosen.outcome is ExchangeOutcome.COMPLETED
    assert chosen.state.session.mode is CoachingMode.DIRECT_EXPLANATION


async def test_choosing_direct_explanation_produces_the_explanation(world: World) -> None:
    """The learner asked to be told; making them type "go on" first would be theatre (§16)."""
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 5)

    chosen = await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)

    assert chosen.reply is not None
    assert chosen.reply.role is MessageRole.COACH
    assert chosen.reply.mode == "DIRECT_EXPLANATION"
    assert world.llm.last_request.mode == "DIRECT_EXPLANATION"


async def test_the_explanation_turn_is_not_counted_as_an_exchange(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 5)

    chosen = await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)

    assert chosen.state.session.exchange_count == 5


async def test_the_direct_explanation_still_has_no_answer_key(world: World) -> None:
    """§16: a direct explanation teaches the concept; it does not unlock hidden data."""
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 5)

    await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)
    request = world.llm.last_request

    assert "You have NOT been given the answer key" in request.system_prompt
    assert "correct_option_ids" not in str(request.context)


async def test_the_learner_can_go_back_to_socratic_coaching(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 5)
    await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)

    back = await world.choose(session_id, CoachingMode.SOCRATIC)

    assert back.state.session.mode is CoachingMode.SOCRATIC
    # Switching back does not generate a turn — the next message is simply coached.
    assert back.reply is None


async def test_subsequent_messages_use_the_chosen_mode(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 5)
    await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)

    await world.say(session_id, "Can you give me another example?")

    assert world.llm.last_request.mode == "DIRECT_EXPLANATION"


async def test_the_transition_is_recorded_once(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    await world.exchange_n_times(session_id, 7)
    offered = world.activity.of_type(CoachingActivityType.DIRECT_EXPLANATION_OFFERED.value)

    assert len(offered) == 1
    assert offered[0].exchange_count == 5


async def test_a_failed_exchange_does_not_advance_the_threshold(world: World) -> None:
    """An outage must not push a learner closer to being offered the answer (§15, §28)."""
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 4)

    world.llm.go_offline(times=1)
    failed = await world.say(session_id, "Still unsure.")

    assert failed.outcome is ExchangeOutcome.UNAVAILABLE
    assert failed.state.session.exchange_count == 4
    assert failed.state.session.direct_explanation_available is False


async def test_the_threshold_is_configurable(world: World) -> None:
    """The number is policy, not a constant buried in a service."""
    settings = Settings(direct_explanation_threshold=2, environment="test")
    configured = build_world(settings=settings)
    configured.given_standard_quiz()
    session_id = (await configured.start(Q_MULTI)).state.session.session_id

    await configured.exchange_n_times(session_id, 2)
    state = await configured.coaching.get_session(
        learner_id="learner-1", session_id=session_id
    )

    assert state.session.direct_explanation_available is True


async def test_a_running_session_keeps_the_threshold_it_started_with(world: World) -> None:
    """Changing configuration must not move the goalposts mid-conversation (§15)."""
    world.given_standard_quiz()
    session = (await world.start(Q_MULTI)).state.session

    assert session.direct_explanation_threshold == 5
