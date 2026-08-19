"""AI service failure, retry and idempotency (§27, §28, §29, §30, §33).

The rule these tests exist to hold: **when the coach cannot answer, nothing is invented and nothing
is lost.** The learner keeps their session, their message and their exchange count; the quiz result
is not touched; and the client is told it may try again.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.modules.coaching.domain.enums import (
    CoachingMode,
    CoachingSessionStatus,
    ExchangeOutcome,
    MessageRole,
    SessionOutcome,
)
from app.modules.coaching.domain.errors import (
    CoachingServiceUnavailableError,
    CoachingSessionNotFoundError,
    CoachingSessionStateConflictError,
    CoachingTimeoutError,
    ExchangeLimitReachedError,
)
from app.modules.coaching.domain.session import new_session
from app.modules.coaching.integration.llm import CoachingRequest, UnconfiguredCoachingLLM
from tests.coaching.world import ATTEMPT_1, LEARNER, OTHER_LEARNER, Q_MULTI, World, build_world

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# The unconfigured provider (§6, §27)
# ---------------------------------------------------------------------------


async def test_the_default_provider_reports_itself_unavailable() -> None:
    llm = UnconfiguredCoachingLLM()

    assert await llm.is_available() is False
    assert llm.configured is False


async def test_the_default_provider_never_invents_a_reply() -> None:
    llm = UnconfiguredCoachingLLM()

    with pytest.raises(CoachingServiceUnavailableError):
        await llm.generate_response(
            CoachingRequest(system_prompt="policy", context={}, session_id="session-1")
        )


# ---------------------------------------------------------------------------
# Failure during the opening turn (§27, §28)
# ---------------------------------------------------------------------------


async def test_an_outage_mid_start_leaves_a_recoverable_session(world: World) -> None:
    world.given_standard_quiz()
    world.llm.go_offline(times=1)

    started = await world.start(Q_MULTI)

    assert started.outcome is SessionOutcome.UNAVAILABLE
    assert started.coaching_available is False
    assert started.unavailable_reason == "COACHING_SERVICE_UNAVAILABLE"
    assert started.state.session.status is CoachingSessionStatus.UNAVAILABLE
    assert started.state.transcript.messages == ()


async def test_no_fake_reply_is_produced_on_failure(world: World) -> None:
    world.given_standard_quiz()
    world.llm.go_offline()

    started = await world.start(Q_MULTI)

    assert started.state.transcript.messages == ()
    assert started.as_dict()["messages"] == []


async def test_a_timeout_is_reported_as_a_timeout(world: World) -> None:
    world.given_standard_quiz()
    world.llm.time_out()

    started = await world.start(Q_MULTI)

    assert started.unavailable_reason == "COACHING_TIMEOUT"


async def test_a_raw_provider_exception_becomes_a_controlled_state(world: World) -> None:
    """An adapter that throws a vendor error must not become a 500 with a stack trace (§29)."""
    world.given_standard_quiz()
    world.llm.fail_with(RuntimeError("connection reset by peer"), times=1)

    started = await world.start(Q_MULTI)

    assert started.outcome is SessionOutcome.UNAVAILABLE
    assert started.unavailable_reason == "COACHING_SERVICE_UNAVAILABLE"


async def test_a_bare_timeout_error_becomes_a_coaching_timeout(world: World) -> None:
    world.given_standard_quiz()
    world.llm.fail_with(TimeoutError(), times=1)

    started = await world.start(Q_MULTI)

    assert started.unavailable_reason == "COACHING_TIMEOUT"


async def test_retry_after_an_outage_produces_the_opening_question(world: World) -> None:
    world.given_standard_quiz()
    world.llm.go_offline(times=1)
    started = await world.start(Q_MULTI)
    session_id = started.state.session.session_id

    retried = await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    assert retried.outcome is ExchangeOutcome.COMPLETED
    assert retried.state.session.status is CoachingSessionStatus.ACTIVE
    assert len(retried.state.transcript.messages) == 1


async def test_retrying_does_not_create_a_second_session(world: World) -> None:
    world.given_standard_quiz()
    world.llm.go_offline(times=1)
    first = await world.start(Q_MULTI)

    second = await world.start(Q_MULTI)

    assert second.state.session.session_id == first.state.session.session_id
    assert len(await world.sessions.list_for_attempt(LEARNER, ATTEMPT_1)) == 1


# ---------------------------------------------------------------------------
# Failure during an exchange (§28)
# ---------------------------------------------------------------------------


async def test_a_failed_exchange_keeps_the_learners_message(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    world.llm.go_offline(times=1)

    failed = await world.say(session_id, "I picked B because it felt proactive.")

    assert failed.outcome is ExchangeOutcome.UNAVAILABLE
    assert failed.retryable is True
    messages = failed.state.transcript.messages
    assert messages[-1].role is MessageRole.LEARNER
    assert messages[-1].content == "I picked B because it felt proactive."


async def test_a_failed_exchange_does_not_count(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    world.llm.go_offline(times=1)

    failed = await world.say(session_id, "Why not B?")

    assert failed.state.session.exchange_count == 0


async def test_retry_answers_the_message_that_was_lost(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    world.llm.go_offline(times=1)
    await world.say(session_id, "I picked B because it felt proactive.")

    retried = await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    assert retried.outcome is ExchangeOutcome.COMPLETED
    assert retried.state.session.exchange_count == 1
    assert world.llm.last_request.conversation[-1]["content"] == (
        "I picked B because it felt proactive."
    )


async def test_retry_does_not_duplicate_the_learners_message(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    world.llm.go_offline(times=1)
    await world.say(session_id, "Why not B?")

    await world.coaching.retry(learner_id=LEARNER, session_id=session_id)
    messages = (
        await world.coaching.get_session(learner_id=LEARNER, session_id=session_id)
    ).transcript.messages

    learner_turns = [item for item in messages if item.role is MessageRole.LEARNER]
    assert len(learner_turns) == 1


async def test_retrying_a_healthy_session_is_a_no_op(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    calls_before = world.llm.call_count

    retried = await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    assert retried.outcome is ExchangeOutcome.COMPLETED
    assert world.llm.call_count == calls_before
    assert retried.state.session.exchange_count == 0


async def test_repeated_failures_park_the_session_as_failed(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    limit = world.container.settings.coaching_max_consecutive_failures
    world.llm.go_offline()

    await world.say(session_id, "One.")
    for _ in range(limit - 1):
        await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    session = await world.sessions.get(session_id)
    assert session.status is CoachingSessionStatus.FAILED
    assert session.consecutive_failures >= limit


async def test_a_failed_session_recovers_on_retry(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    limit = world.container.settings.coaching_max_consecutive_failures
    world.llm.go_offline(times=limit)
    await world.say(session_id, "One.")
    for _ in range(limit - 1):
        await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    recovered = await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    assert recovered.outcome is ExchangeOutcome.COMPLETED
    assert recovered.state.session.status is CoachingSessionStatus.ACTIVE
    assert recovered.state.session.consecutive_failures == 0


async def test_a_failed_session_will_not_accept_a_new_message_until_retried(
    world: World,
) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    limit = world.container.settings.coaching_max_consecutive_failures
    world.llm.go_offline()
    await world.say(session_id, "One.")
    for _ in range(limit - 1):
        await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    with pytest.raises(CoachingSessionStateConflictError) as error:
        await world.say(session_id, "Two.")

    assert error.value.context["status"] == "FAILED"


async def test_nothing_about_the_quiz_result_changes_during_an_outage(world: World) -> None:
    """§27: do not lose the quiz result, do not modify the score."""
    world.given_standard_quiz()
    before = world.scores.scores[ATTEMPT_1]
    world.llm.go_offline()

    await world.start(Q_MULTI)

    assert world.scores.scores[ATTEMPT_1] == before
    assert world.feedback.records[ATTEMPT_1].available is True


# ---------------------------------------------------------------------------
# Idempotency (§30)
# ---------------------------------------------------------------------------


async def test_starting_twice_resumes_the_same_session(world: World) -> None:
    world.given_standard_quiz()

    first = await world.start(Q_MULTI)
    second = await world.start(Q_MULTI)

    assert first.outcome is SessionOutcome.STARTED
    assert second.outcome is SessionOutcome.RESUMED
    assert second.state.session.session_id == first.state.session.session_id


async def test_resuming_does_not_produce_a_second_opening_question(world: World) -> None:
    world.given_standard_quiz()

    await world.start(Q_MULTI)
    calls_after_first = world.llm.call_count
    resumed = await world.start(Q_MULTI)

    assert world.llm.call_count == calls_after_first
    assert len(resumed.state.transcript.messages) == 1


async def test_a_duplicate_insert_converges_on_the_winner(world: World) -> None:
    """Two concurrent starts must not fork the conversation (§30)."""
    world.given_standard_quiz()
    first = await world.start(Q_MULTI)

    # Simulate the losing side of a race: a second insert for the same natural key.
    with pytest.raises(Exception) as error:
        await world.sessions.insert(
            new_session(
                session_id="session-rogue",
                learner_id=LEARNER,
                attempt_id=ATTEMPT_1,
                course_id="course-1",
                question_id=Q_MULTI,
                now="2026-02-01T08:00:00Z",
            )
        )

    assert getattr(error.value, "code", None) == "DUPLICATE_COACHING_SESSION"
    assert (
        await world.sessions.find_open(LEARNER, ATTEMPT_1, Q_MULTI)
    ).session_id == first.state.session.session_id


async def test_a_concurrent_start_resumes_the_winner(world: World) -> None:
    """The losing side of a race must read the winner, not overwrite it (§30).

    Reproduced by making the natural-key lookup miss once — exactly what happens when two requests
    check for an existing session at the same moment and both find nothing.
    """

    world.given_standard_quiz()
    first = await world.start(Q_MULTI)

    real_find_open = world.sessions.find_open
    missed = {"once": False}

    async def find_open_missing_once(learner_id: str, attempt_id: str, question_id: str):  # noqa: ANN202
        if not missed["once"]:
            missed["once"] = True
            return None
        return await real_find_open(learner_id, attempt_id, question_id)

    world.sessions.find_open = find_open_missing_once  # type: ignore[method-assign]
    try:
        contested = await world.start(Q_MULTI)
    finally:
        world.sessions.find_open = real_find_open  # type: ignore[method-assign]

    assert contested.outcome is SessionOutcome.RESUMED
    assert contested.state.session.session_id == first.state.session.session_id
    assert len(await world.sessions.list_for_attempt(LEARNER, ATTEMPT_1)) == 1


async def test_a_parked_session_is_recovered_without_another_model_call(world: World) -> None:
    """Retrying a FAILED session whose coach turn did land recovers it and asks nothing (§28)."""
    world.given_standard_quiz()
    started = await world.start(Q_MULTI)
    session_id = started.state.session.session_id
    parked = started.state.session.with_failure(
        "COACHING_SERVICE_UNAVAILABLE", "2026-02-01T08:01:00Z", limit=1
    )
    await world.sessions.update(parked)
    calls_before = world.llm.call_count

    recovered = await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    assert recovered.outcome is ExchangeOutcome.COMPLETED
    assert recovered.state.session.status is CoachingSessionStatus.ACTIVE
    assert recovered.reply is not None
    assert world.llm.call_count == calls_before


async def test_a_completed_session_will_not_change_mode(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.exchange_n_times(session_id, 5)
    await world.coaching.complete_session(learner_id=LEARNER, session_id=session_id)

    with pytest.raises(CoachingSessionStateConflictError) as error:
        await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)

    assert error.value.context["action"] == "change mode"


async def test_each_question_gets_its_own_session(world: World) -> None:
    world.given_standard_quiz()

    first = await world.start(Q_MULTI)
    second = await world.start("q-true-false")

    assert first.state.session.session_id != second.state.session.session_id
    assert len(await world.sessions.list_for_attempt(LEARNER, ATTEMPT_1)) == 2


# ---------------------------------------------------------------------------
# Session access and limits (§9, §29)
# ---------------------------------------------------------------------------


async def test_another_learners_session_is_not_found(world: World) -> None:
    """Not "forbidden" — a probe must not distinguish "not yours" from "does not exist" (§9)."""
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id

    with pytest.raises(CoachingSessionNotFoundError):
        await world.coaching.get_session(learner_id=OTHER_LEARNER, session_id=session_id)


async def test_an_unknown_session_is_not_found(world: World) -> None:
    with pytest.raises(CoachingSessionNotFoundError):
        await world.coaching.get_session(learner_id=LEARNER, session_id="nope")


async def test_a_completed_session_will_not_accept_messages(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.coaching.complete_session(learner_id=LEARNER, session_id=session_id)

    with pytest.raises(CoachingSessionStateConflictError):
        await world.say(session_id, "One more thing.")


async def test_a_completed_session_will_not_be_retried(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    await world.coaching.complete_session(learner_id=LEARNER, session_id=session_id)

    with pytest.raises(CoachingSessionStateConflictError):
        await world.coaching.retry(learner_id=LEARNER, session_id=session_id)


async def test_the_exchange_ceiling_is_enforced(world: World) -> None:
    """A runaway guard, not a teaching rule."""
    limited = build_world(
        settings=Settings(coaching_max_exchanges=2, environment="test")
    )
    limited.given_standard_quiz()
    session_id = (await limited.start(Q_MULTI)).state.session.session_id
    await limited.exchange_n_times(session_id, 2)

    with pytest.raises(ExchangeLimitReachedError) as error:
        await limited.say(session_id, "And another.")

    assert error.value.context["limit"] == 2


async def test_a_session_can_still_be_read_while_the_ai_is_down(world: World) -> None:
    world.given_standard_quiz()
    session_id = (await world.start(Q_MULTI)).state.session.session_id
    world.llm.available = False

    state = await world.coaching.get_session(learner_id=LEARNER, session_id=session_id)

    assert len(state.transcript.messages) == 1


async def test_a_timeout_error_carries_no_provider_detail(world: World) -> None:
    """An AI error body can echo the prompt; it must never be forwarded (§29)."""
    error = CoachingTimeoutError(session_id="session-1", timeout_seconds=20.0)

    assert "prompt" not in error.message.lower()
    assert error.to_response()["error"]["retryable"] is True
    assert "timeout_seconds" not in error.to_response()["error"].get("context", {})
