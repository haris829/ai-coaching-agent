"""Requirements 2, 3, 4, 7, 8, 11 - four-part answer, context integration,
practice-area personalisation, follow-up actions, response contract, rating."""

from __future__ import annotations

import pytest

from uc03.adapters.mocks import (
    MockContextProvider,
    context_without_naric,
    context_without_practice_area,
    full_context,
)
from uc03.domain.enums import (
    DEFAULT_NARIC_LEVEL,
    ExplanationDepth,
    FieldAvailability,
    FollowUpAction,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    ResponseStatus,
)
from uc03.explanation import depth_for
from uc03.service import DEGRADED_CONTEXT, DEGRADED_PERSONALISATION

from .conftest import ALICE_SESSION, build_service

QUESTION = "What is negligence in tort law?"


async def _answer(service, alice, question: str = QUESTION):
    return await service.answer(
        question=question, session_id=ALICE_SESSION, principal=alice
    )


async def test_all_four_parts_are_present_and_separate(service, alice):
    response = await _answer(service, alice)
    assert response.status is ResponseStatus.ANSWERED
    parts = response.parts
    assert parts is not None
    assert parts.plain_english.strip()
    assert parts.formal_definition.strip()
    assert parts.practice_example.strip()
    assert parts.authority is not None
    # Separate fields, not one blob - a frontend can render each on its own.
    assert parts.plain_english != parts.formal_definition != parts.practice_example


async def test_four_parts_present_for_every_answered_classification(service, alice):
    for question in (
        "What is negligence in tort law?",
        "How do I file a claim in the small claims court?",
        "What does mens rea mean?",
    ):
        response = await _answer(service, alice, question)
        assert response.status is ResponseStatus.ANSWERED, question
        assert response.parts is not None
        for value in (
            response.parts.plain_english,
            response.parts.formal_definition,
            response.parts.practice_example,
        ):
            assert value.strip()
        assert response.parts.authority.status is not None


@pytest.mark.parametrize(
    ("level", "expected_depth"),
    [
        (NaricLevel.LEVEL_3, ExplanationDepth.FOUNDATION),
        (NaricLevel.LEVEL_6, ExplanationDepth.INTERMEDIATE),
        (NaricLevel.LEVEL_7, ExplanationDepth.ADVANCED),
        (NaricLevel.LEVEL_7_PLUS, ExplanationDepth.ADVANCED),
        (DEFAULT_NARIC_LEVEL, ExplanationDepth.FOUNDATION),
    ],
)
def test_naric_level_maps_deterministically_to_depth(level, expected_depth):
    assert depth_for(level) is expected_depth
    assert depth_for(level) is depth_for(level)  # pure function


async def test_explanations_differ_by_naric_level(alice):
    """Requirement 2: adapt to the learner's level via the depth abstraction,
    not by random rewording."""
    texts: dict[NaricLevel, str] = {}
    for level in (NaricLevel.LEVEL_3, NaricLevel.LEVEL_6, NaricLevel.LEVEL_7_PLUS):

        def builder(user_id, session_id, _level=level):
            return full_context(user_id, session_id).model_copy(
                update={"naric_level": _level}
            )

        svc = build_service(context_provider=MockContextProvider(builder=builder))
        response = await _answer(svc, alice)
        texts[level] = response.parts.plain_english
        assert response.meta.explanation_depth is depth_for(level)

    assert len({*texts.values()}) == 3, "each depth must produce a distinct explanation"
    # Foundation states no prior study is needed; advanced speaks doctrinally.
    assert "prior legal study" in texts[NaricLevel.LEVEL_3].lower()
    assert "doctrinally" in texts[NaricLevel.LEVEL_7_PLUS].lower()
    assert "doctrinally" not in texts[NaricLevel.LEVEL_3].lower()


async def test_same_level_and_framing_produce_identical_explanation(alice):
    """Depth adaptation is deterministic, not random rewording.

    Determinism holds for a given (level, framing) pair. Asking the same
    question twice in one session deliberately does NOT repeat itself - the
    framing registry hands out a new framing - so the two services below each
    get a fresh registry to isolate the depth behaviour.
    """
    first = await _answer(build_service(), alice)
    second = await _answer(build_service(), alice)
    assert first.meta.framing is second.meta.framing
    assert first.parts.plain_english == second.parts.plain_english


async def test_repeating_a_question_in_one_session_does_not_repeat_the_framing(alice):
    svc = build_service()
    first = await _answer(svc, alice)
    second = await _answer(svc, alice)
    assert first.meta.framing is not second.meta.framing
    assert first.parts.plain_english != second.parts.plain_english


async def test_practice_area_personalises_the_example(alice):
    svc = build_service(context_provider=MockContextProvider(builder=full_context))
    response = await _answer(svc, alice)
    assert "employment" in response.parts.practice_example.lower()
    assert response.meta.personalisation_applied is True
    assert response.meta.practice_area_availability is FieldAvailability.PROVIDED


async def test_missing_practice_area_gives_general_example_and_records_it(alice):
    svc = build_service(
        context_provider=MockContextProvider(builder=context_without_practice_area)
    )
    response = await _answer(svc, alice)
    example = response.parts.practice_example.lower()
    assert "general example" in example
    assert "employment" not in example, "must not invent a speciality"
    assert response.meta.personalisation_applied is False
    assert response.meta.practice_area_availability is FieldAvailability.MISSING
    assert DEGRADED_PERSONALISATION in response.meta.degraded


async def test_missing_naric_falls_back_to_safe_default_and_records_it(alice):
    svc = build_service(context_provider=MockContextProvider(builder=context_without_naric))
    response = await _answer(svc, alice)
    # The level is a real level; `source` is what says it was defaulted.
    assert response.meta.naric_level is DEFAULT_NARIC_LEVEL
    assert response.meta.naric_level_source is NaricLevelSource.DEFAULT
    # Safe default is the most accessible depth, never assumed expertise.
    assert response.meta.explanation_depth is ExplanationDepth.FOUNDATION


async def test_context_provider_failure_degrades_safely(alice):
    svc = build_service(context_provider=MockContextProvider(fail=True))
    response = await _answer(svc, alice)
    assert response.status is ResponseStatus.ANSWERED
    assert response.meta.naric_level is DEFAULT_NARIC_LEVEL
    assert response.meta.naric_level_source is NaricLevelSource.DEFAULT
    assert response.meta.practice_area_availability is FieldAvailability.PROVIDER_UNAVAILABLE
    assert DEGRADED_CONTEXT in response.meta.degraded
    assert response.meta.personalisation_applied is False


async def test_all_three_follow_up_actions_on_success(service, alice):
    response = await _answer(service, alice)
    assert set(response.follow_up_actions) == {
        FollowUpAction.EXPLAIN_DIFFERENTLY,
        FollowUpAction.ANOTHER_EXAMPLE,
        FollowUpAction.GO_DEEPER,
    }
    assert len(response.follow_up_actions) == 3


async def test_rating_state_is_pending(service, alice):
    response = await _answer(service, alice)
    assert response.rating_state is RatingState.PENDING


async def test_response_contract_shape(service, alice):
    response = await _answer(service, alice)
    payload = response.model_dump(mode="json")
    assert payload["question_id"]
    assert payload["classification"] == "legal_concept"
    assert payload["status"] == "answered"
    assert set(payload["parts"]) == {
        "plain_english",
        "formal_definition",
        "practice_example",
        "authority",
    }
    assert payload["rating_state"] == "pending"
    assert payload["follow_up_actions"] == [
        "explain_differently",
        "another_example",
        "go_deeper",
    ]
