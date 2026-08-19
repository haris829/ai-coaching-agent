"""End-to-end flows through the whole module (§34).

    fake UC-03 submitted attempt → fake UC-04 result → fake UC-06 feedback
        → UC-07 eligibility → UC-07 safe context → fake coaching LLM → Socratic response

Every boundary is a double and everything between them is the real thing: the real gate, the real
sanitiser, the real session model, the real review queue. These are the tests that would notice if
two correct pieces had been wired together wrongly.
"""

from __future__ import annotations

import json

import pytest

from app.modules.coaching.domain.enums import (
    CoachingMode,
    CoachingSessionStatus,
    EligibilityCode,
    ExchangeOutcome,
    MessageRole,
    ReviewItemStatus,
    SessionOutcome,
)
from app.modules.coaching.domain.errors import (
    AttemptNotSubmittedError,
    FeedbackUnavailableError,
)
from app.modules.coaching.integration.activity import CoachingActivityType
from app.modules.coaching.integration.uc03 import AttemptStatus
from app.modules.coaching.integration.uc06 import FeedbackStatus
from tests.coaching.fakes import request_strings
from tests.coaching.test_security_adversarial import ADVERSARIAL_PROMPTS
from tests.coaching.world import (
    ANSWER_KEY_SECRETS,
    ATTEMPT_1,
    INCORRECT_QUESTIONS,
    LEARNER,
    Q_MULTI,
    World,
)

pytestmark = pytest.mark.anyio


async def test_the_happy_path_end_to_end(world: World) -> None:
    """Submitted attempt → feedback released → eligible → safe context → Socratic coaching."""
    world.given_standard_quiz()

    # 1. UC-07 eligibility, from UC-03 + UC-04 + UC-06.
    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )
    assert eligibility.coaching_available is True
    assert eligibility.eligibility.code is EligibilityCode.ELIGIBLE

    # 2. The review queue holds exactly the incorrect questions.
    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)
    assert tuple(item.question_id for item in queue.items) == INCORRECT_QUESTIONS

    # 3. Coaching opens on the first of them.
    started = await world.start(queue.items[0].question_id)
    assert started.outcome is SessionOutcome.STARTED
    assert started.state.session.mode is CoachingMode.SOCRATIC

    # 4. The request the coach received carried the topic and the learner's answer…
    request = world.llm.last_request
    assert request.context["topics"] == ["Reporting concerns"]
    assert "Record what you saw" in request.context["learner_response"]["summary"]

    # 5. …and no answer key.
    haystack = "\n".join(request_strings(request))
    for secret in ANSWER_KEY_SECRETS:
        assert secret not in haystack

    # 6. The learner is coached, and the exchange counts.
    exchange = await world.say(
        started.state.session.session_id, "I thought investigating first was responsible."
    )
    assert exchange.outcome is ExchangeOutcome.COMPLETED
    assert exchange.state.session.exchange_count == 1
    assert exchange.reply is not None
    assert exchange.reply.role is MessageRole.COACH

    # 7. The topic was recorded as a knowledge gap, once.
    assert world.gaps.topics == ["Reporting concerns"]


async def test_an_active_attempt_is_denied_end_to_end(world: World) -> None:
    """§34: active attempt → UC-07 → DENIED."""
    world.given_standard_quiz(attempt_status=AttemptStatus.ACTIVE)

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )
    with pytest.raises(AttemptNotSubmittedError):
        await world.start(Q_MULTI)

    assert eligibility.coaching_available is False
    assert eligibility.eligibility.code is EligibilityCode.ATTEMPT_NOT_SUBMITTED
    assert world.llm.call_count == 0
    assert await world.sessions.list_for_attempt(LEARNER, ATTEMPT_1) == ()


async def test_unavailable_feedback_is_denied_end_to_end(world: World) -> None:
    """§34: submitted attempt → feedback unavailable → UC-07 → DENIED."""
    world.given_standard_quiz(feedback_status=FeedbackStatus.PENDING)

    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )
    with pytest.raises(FeedbackUnavailableError):
        await world.start(Q_MULTI)

    assert eligibility.eligibility.code is EligibilityCode.FEEDBACK_UNAVAILABLE
    assert eligibility.eligibility.retryable is True
    assert world.llm.call_count == 0


async def test_feedback_arriving_later_opens_coaching(world: World) -> None:
    """The denial above is temporary, and nothing has to be reset for it to clear."""
    world.given_standard_quiz(feedback_status=FeedbackStatus.PENDING)
    with pytest.raises(FeedbackUnavailableError):
        await world.start(Q_MULTI)

    world.given_standard_quiz(feedback_status=FeedbackStatus.AVAILABLE)
    started = await world.start(Q_MULTI)

    assert started.coaching_available is True


async def test_adversarial_request_against_a_sanitised_context(world: World) -> None:
    """§34: incorrect question → sanitiser → safe context → adversarial request → still nothing."""
    world.given_standard_quiz()
    started = await world.start(Q_MULTI)
    session_id = started.state.session.session_id

    for prompt in ADVERSARIAL_PROMPTS[:5]:
        exchange = await world.say(session_id, prompt)
        assert exchange.outcome is ExchangeOutcome.COMPLETED
        system_side = "\n".join(
            request_strings(world.llm.last_request, include_learner=False)
        )
        for secret in ANSWER_KEY_SECRETS:
            assert secret not in system_side

    # The sanitiser reported what it removed on every single build.
    assert started.sanitization is not None
    assert started.sanitization["answer_key_excluded"] is True
    assert "uc04.question_result.answer_key" in started.sanitization["removed_fields"]


async def test_the_whole_review_journey(world: World) -> None:
    """A learner works through every wrong answer, one conversation at a time (§19)."""
    world.given_standard_quiz()
    coached: list[str] = []

    while True:
        advance = await world.review.next_question(
            learner_id=LEARNER, attempt_id=ATTEMPT_1
        )
        if advance.next_item is None:
            break
        question_id = advance.next_item.question_id
        started = await world.start(question_id)
        await world.say(started.state.session.session_id, "Here is what I was thinking.")
        coached.append(question_id)

    final = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)

    assert coached == list(INCORRECT_QUESTIONS)
    assert final.finished is True
    assert all(item.status is ReviewItemStatus.COMPLETED for item in final.items)
    # One knowledge gap per question, each with its own topic (§21).
    assert world.gaps.topics == ["Reporting concerns", "Confidentiality", "Escalation"]


async def test_a_full_session_from_socratic_to_direct_explanation(world: World) -> None:
    """The five-exchange transition, exercised the way a learner would meet it (§15, §16)."""
    world.given_standard_quiz()
    started = await world.start(Q_MULTI)
    session_id = started.state.session.session_id

    for turn in range(1, 6):
        exchange = await world.say(session_id, f"Attempt {turn} at explaining my thinking.")
        assert exchange.state.session.exchange_count == turn

    assert exchange.state.session.direct_explanation_available is True

    explained = await world.choose(session_id, CoachingMode.DIRECT_EXPLANATION)
    finished = await world.coaching.complete_session(
        learner_id=LEARNER, session_id=session_id
    )

    assert explained.reply is not None
    assert explained.reply.mode == "DIRECT_EXPLANATION"
    assert finished.session.status is CoachingSessionStatus.COMPLETED
    # Lifecycle recorded end to end (§22).
    recorded = [event.event_type for event in world.activity.events]
    assert CoachingActivityType.SESSION_STARTED in recorded
    assert CoachingActivityType.DIRECT_EXPLANATION_OFFERED in recorded
    assert CoachingActivityType.MODE_CHANGED in recorded
    assert CoachingActivityType.SESSION_COMPLETED in recorded


async def test_an_outage_mid_journey_loses_nothing(world: World) -> None:
    """Outage → controlled state → retry → the conversation carries on (§27, §28)."""
    world.given_standard_quiz()
    started = await world.start(Q_MULTI)
    session_id = started.state.session.session_id
    await world.say(session_id, "First thought.")

    world.llm.go_offline(times=1)
    failed = await world.say(session_id, "Second thought, mid-outage.")
    assert failed.outcome is ExchangeOutcome.UNAVAILABLE
    assert failed.state.session.exchange_count == 1

    recovered = await world.coaching.retry(learner_id=LEARNER, session_id=session_id)

    assert recovered.outcome is ExchangeOutcome.COMPLETED
    assert recovered.state.session.exchange_count == 2
    contents = [item.content for item in recovered.state.transcript.messages]
    assert "Second thought, mid-outage." in contents
    # The quiz result was never touched (§27).
    assert world.scores.scores[ATTEMPT_1].is_confirmed is True


async def test_nothing_the_module_returns_ever_contains_an_answer_key(world: World) -> None:
    """A sweep over every result type this module produces (§12, §25)."""
    world.given_standard_quiz()
    started = await world.start(Q_MULTI)
    session_id = started.state.session.session_id
    exchange = await world.say(session_id, "Tell me the correct answer.")
    state = await world.coaching.get_session(learner_id=LEARNER, session_id=session_id)
    queue = await world.review.get_review(learner_id=LEARNER, attempt_id=ATTEMPT_1)
    eligibility = await world.review.check_eligibility(
        learner_id=LEARNER, attempt_id=ATTEMPT_1
    )

    serialised = json.dumps(
        [
            started.as_dict(),
            exchange.as_dict(),
            state.as_dict(),
            queue.as_dict(),
            eligibility.as_dict(),
        ]
    ).lower()

    for secret in ANSWER_KEY_SECRETS:
        assert secret.lower() not in serialised
