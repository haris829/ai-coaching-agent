"""G1 - follow-up actions as real operations.

Covers: each action produces a materially different response; no framing
repeats within a session; a paraphrase-only generator response is rejected;
exhaustion is stated honestly; `follow_up_of` linkage is correct.
"""

from __future__ import annotations

import pytest

from uc03.adapters.mocks import (
    InMemoryFramingRegistry,
    InMemoryQuestionLogger,
    MockContextProvider,
    ParaphraseGenerator,
    context_without_practice_area,
)
from uc03.distinctness import overlap
from uc03.domain.enums import (
    ALL_FRAMINGS,
    ExplanationDepth,
    FollowUpAction,
    FramingStrategy,
    ResponseStatus,
)
from uc03.errors import AuthorizationError, InteractionNotFoundError, InputValidationError

from .conftest import ALICE_SESSION, BOB_SESSION, build_service

QUESTION = "What is negligence in tort law?"


async def _ask(svc, alice, question: str = QUESTION, session: str = ALICE_SESSION):
    return await svc.answer(question=question, session_id=session, principal=alice)


async def _follow(svc, alice, qid, action, session: str = ALICE_SESSION):
    return await svc.follow_up(
        question_id=qid, action=action, session_id=session, principal=alice
    )


# --- the operation exists and is anchored --------------------------------


async def test_follow_up_returns_a_new_answer_linked_to_the_original(alice):
    svc = build_service()
    first = await _ask(svc, alice)
    second = await _follow(svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY)

    assert second.status is ResponseStatus.ANSWERED
    assert second.parts is not None
    assert second.question_id != first.question_id
    assert second.follow_up_of == first.question_id


async def test_follow_up_linkage_is_recorded_on_the_log(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    first = await _ask(svc, alice)
    second = await _follow(svc, alice, first.question_id, FollowUpAction.GO_DEEPER)

    record = logger.last
    assert record.question_id == second.question_id
    assert record.follow_up_of == first.question_id
    assert record.follow_up_action is FollowUpAction.GO_DEEPER
    assert record.framing is not None
    assert record.concept_key == logger.records[0].concept_key


@pytest.mark.parametrize("action", list(FollowUpAction))
async def test_every_action_produces_a_materially_different_response(action, alice):
    svc = build_service()
    first = await _ask(svc, alice)
    second = await _follow(svc, alice, first.question_id, action)

    assert second.status is ResponseStatus.ANSWERED
    assert second.parts.plain_english != first.parts.plain_english
    assert second.meta.framing is not first.meta.framing
    # Not merely reworded: content overlap must be well below the repeat bar.
    assert overlap(second.parts.plain_english, first.parts.plain_english) < 0.6


async def test_another_example_changes_the_worked_example(alice):
    svc = build_service()
    first = await _ask(svc, alice)
    second = await _follow(svc, alice, first.question_id, FollowUpAction.ANOTHER_EXAMPLE)
    assert second.parts.practice_example != first.parts.practice_example


async def test_go_deeper_increases_the_explanation_depth(alice):
    """A FOUNDATION learner asking to go deeper gets the next depth up."""
    svc = build_service(
        context_provider=MockContextProvider(builder=context_without_practice_area)
    )
    first = await _ask(svc, alice)
    assert first.meta.explanation_depth is ExplanationDepth.FOUNDATION

    second = await _follow(svc, alice, first.question_id, FollowUpAction.GO_DEEPER)
    assert second.meta.explanation_depth is ExplanationDepth.INTERMEDIATE


async def test_go_deeper_at_the_deepest_level_stays_put_but_reframes(alice):
    svc = build_service()  # full_context is LEVEL_7 -> ADVANCED
    first = await _ask(svc, alice)
    assert first.meta.explanation_depth is ExplanationDepth.ADVANCED

    second = await _follow(svc, alice, first.question_id, FollowUpAction.GO_DEEPER)
    assert second.meta.explanation_depth is ExplanationDepth.ADVANCED
    assert second.meta.framing is not first.meta.framing


# --- never repeat a framing ----------------------------------------------


async def test_no_framing_repeats_within_a_session(alice):
    svc = build_service()
    first = await _ask(svc, alice)
    seen = [first.meta.framing]
    texts = [first.parts.plain_english]

    for _ in range(len(ALL_FRAMINGS) - 1):
        nxt = await _follow(
            svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY
        )
        assert nxt.status is ResponseStatus.ANSWERED
        assert nxt.meta.framing not in seen, f"framing {nxt.meta.framing} repeated"
        seen.append(nxt.meta.framing)
        texts.append(nxt.parts.plain_english)

    assert len(set(seen)) == len(ALL_FRAMINGS)
    assert len(set(texts)) == len(ALL_FRAMINGS), "every framing gave distinct prose"


async def test_mixed_actions_still_never_repeat_a_framing(alice):
    """The rule binds across actions, not per action."""
    svc = build_service()
    first = await _ask(svc, alice)
    seen = {first.meta.framing}

    actions = [
        FollowUpAction.ANOTHER_EXAMPLE,
        FollowUpAction.GO_DEEPER,
        FollowUpAction.EXPLAIN_DIFFERENTLY,
        FollowUpAction.ANOTHER_EXAMPLE,
        FollowUpAction.GO_DEEPER,
    ]
    for action in actions:
        nxt = await _follow(svc, alice, first.question_id, action)
        assert nxt.meta.framing not in seen
        seen.add(nxt.meta.framing)


async def test_framing_state_is_scoped_to_the_session(alice):
    """A different session starts fresh - state is per session, per concept."""
    svc = build_service()
    first = await _ask(svc, alice, session=ALICE_SESSION)
    other = await _ask(svc, alice, session="session-alice-2")
    assert other.meta.framing is first.meta.framing


async def test_framing_state_is_scoped_to_the_concept(alice):
    svc = build_service()
    first = await _ask(svc, alice, QUESTION)
    other = await _ask(svc, alice, "What is consideration in contract law?")
    # A different concept is untouched by the first concept's history.
    assert other.meta.framing is first.meta.framing
    assert other.meta.framings_used == ()


async def test_registry_is_not_generator_memory(alice):
    """Framing state survives replacing the generator instance."""
    registry = InMemoryFramingRegistry()
    first = await _ask(build_service(framing_registry=registry), alice)
    second_svc = build_service(framing_registry=registry)
    second = await _ask(second_svc, alice)
    assert second.meta.framing is not first.meta.framing


# --- exhaustion ----------------------------------------------------------


async def test_exhaustion_is_stated_and_never_cycles(alice):
    svc = build_service()
    first = await _ask(svc, alice)
    for _ in range(len(ALL_FRAMINGS) - 1):
        await _follow(svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY)

    exhausted = await _follow(
        svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY
    )
    assert exhausted.status is ResponseStatus.FRAMINGS_EXHAUSTED
    assert exhausted.parts is None, "must not silently reuse the first framing"
    assert exhausted.message
    assert exhausted.meta.framings_remaining == 0
    # Honest about what it can still offer.
    lowered = exhausted.message.lower()
    assert "deeper" in lowered or "move on" in lowered
    assert exhausted.follow_up_of == first.question_id


async def test_exhaustion_is_logged(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    first = await _ask(svc, alice)
    for _ in range(len(ALL_FRAMINGS) - 1):
        await _follow(svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY)
    await _follow(svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY)

    assert logger.last.status is ResponseStatus.FRAMINGS_EXHAUSTED
    assert logger.last.answer is None


# --- paraphrase rejection -------------------------------------------------


async def test_paraphrase_only_response_is_rejected(alice):
    """A generator that reuses its text under a new framing label is refused."""
    svc = build_service(generator=ParaphraseGenerator())
    first = await _ask(svc, alice)
    assert first.status is ResponseStatus.ANSWERED

    second = await _follow(svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY)
    assert second.status is ResponseStatus.ERROR
    assert second.parts is None, "the repeat must not reach the learner"
    assert second.retry_available is True
    assert second.message


async def test_rejected_paraphrase_does_not_consume_a_framing(alice):
    registry = InMemoryFramingRegistry()
    svc = build_service(generator=ParaphraseGenerator(), framing_registry=registry)
    first = await _ask(svc, alice)
    used_before = await registry.used_framings(
        session_id=ALICE_SESSION, concept_key=list(registry.used)[0][1]
    )
    await _follow(svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY)
    used_after = await registry.used_framings(
        session_id=ALICE_SESSION, concept_key=list(registry.used)[0][1]
    )
    assert used_after == used_before, "a rejected answer must not burn a framing"


async def test_distinct_prose_is_not_flagged_as_a_paraphrase(alice):
    svc = build_service()
    first = await _ask(svc, alice)
    second = await _follow(svc, alice, first.question_id, FollowUpAction.EXPLAIN_DIFFERENTLY)
    assert second.status is ResponseStatus.ANSWERED


# --- errors and authorisation --------------------------------------------


async def test_unknown_question_id_is_not_found(alice):
    svc = build_service()
    with pytest.raises(InteractionNotFoundError):
        await _follow(svc, alice, "00000000-0000-0000-0000-000000000000",
                      FollowUpAction.EXPLAIN_DIFFERENTLY)


async def test_cannot_follow_up_another_users_interaction(alice):
    """Bob's interaction is indistinguishable from a missing one."""
    svc = build_service()
    bob = await svc.authenticate("dev-token-bob")
    bob_answer = await svc.answer(
        question=QUESTION, session_id=BOB_SESSION, principal=bob
    )
    with pytest.raises(InteractionNotFoundError):
        await _follow(svc, alice, bob_answer.question_id, FollowUpAction.GO_DEEPER)


async def test_follow_up_requires_session_ownership(alice):
    svc = build_service()
    first = await _ask(svc, alice)
    with pytest.raises(AuthorizationError):
        await _follow(
            svc, alice, first.question_id, FollowUpAction.GO_DEEPER, session=BOB_SESSION
        )


async def test_cannot_follow_up_an_unanswered_interaction(alice):
    svc = build_service()
    clarification = await _ask(svc, alice, "Tell me about consideration")
    assert clarification.status is ResponseStatus.CLARIFICATION_NEEDED
    with pytest.raises(InputValidationError):
        await _follow(svc, alice, clarification.question_id, FollowUpAction.GO_DEEPER)


async def test_registry_outage_fails_the_follow_up_rather_than_risking_a_repeat(alice):
    """'Never repeat a framing' is not a best-effort rule."""
    svc = build_service()
    first = await _ask(svc, alice)
    broken = build_service(framing_registry=InMemoryFramingRegistry(fail=True))
    # Reuse the first service's log so the interaction is findable.
    broken._interactions = svc._interactions  # noqa: SLF001 - test wiring
    broken._authorizer = svc._authorizer  # noqa: SLF001

    result = await broken.follow_up(
        question_id=first.question_id,
        action=FollowUpAction.EXPLAIN_DIFFERENTLY,
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert result.status is ResponseStatus.ERROR
    assert result.parts is None
    assert result.retry_available is True


async def test_follow_up_unsupported_when_ports_are_absent(alice):
    from uc03.adapters.mocks import (
        MockLegalAuthorityProvider,
        StaticSessionAuthorizer,
        SystemClock,
    )
    from uc03.adapters.rule_based import (
        RuleBasedClassifier,
        RuleBasedTopicTagger,
        TemplateAnswerGenerator,
    )
    from uc03.service import QAService

    svc = QAService(
        classifier=RuleBasedClassifier(),
        generator=TemplateAnswerGenerator(),
        context_provider=MockContextProvider(),
        authority_provider=MockLegalAuthorityProvider(),
        tagger=RuleBasedTopicTagger(),
        logger=InMemoryQuestionLogger(),
        authorizer=StaticSessionAuthorizer(),
        clock=SystemClock(),
    )
    assert svc.supports_follow_up is False
    with pytest.raises(InputValidationError):
        await svc.follow_up(
            question_id="x",
            action=FollowUpAction.GO_DEEPER,
            session_id=ALICE_SESSION,
            principal=alice,
        )


# --- framing strategy set -------------------------------------------------


def test_framing_strategy_set_is_enumerated():
    assert set(ALL_FRAMINGS) == {
        FramingStrategy.ANALOGY,
        FramingStrategy.WORKED_EXAMPLE,
        FramingStrategy.CONTRAST_NEAR_MISS,
        FramingStrategy.FIRST_PRINCIPLES,
        FramingStrategy.PROCEDURAL_WALKTHROUGH,
        FramingStrategy.MISCONCEPTION_CORRECTION,
    }
    assert len(ALL_FRAMINGS) == 6
