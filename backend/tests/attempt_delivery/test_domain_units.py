"""Focused unit tests for the pure domain logic.

The API tests cover behaviour end to end; these pin down the small pieces that are
easiest to get subtly wrong — canonicalisation, seeded randomisation, clock handling and
the configuration guard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.time import FixedClock, ensure_utc, parse_instant, to_iso
from app.modules.attempt_delivery.domain.answer_validation import (
    canonical_json,
    hash_answer,
    validate_answer,
)
from app.modules.attempt_delivery.domain.enums import QuestionPresentation, QuestionType
from app.modules.attempt_delivery.domain.errors import AppError, ErrorCode
from app.modules.attempt_delivery.domain.rng import make_rng, sample_without_replacement, shuffled
from app.modules.attempt_delivery.integration.uc01.types import QuizConfigurationVersion
from app.modules.attempt_delivery.integration.uc02.types import BankQuestion, QuestionOption
from app.modules.attempt_delivery.services.configuration_lock import lock_configuration
from app.modules.attempt_delivery.services.question_selection_service import is_deliverable
from tests.support.fixtures import (
    drag_to_order_question,
    multi_select_question,
    scenario_question,
    single_choice_question,
    true_false_question,
)

# ---------------------------------------------------------------------------
# Canonicalisation and hashing
# ---------------------------------------------------------------------------


def test_canonical_json_sorts_object_keys() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_json_preserves_array_order() -> None:
    # Array order is significant for drag-to-order, so it must never be normalised away.
    assert canonical_json(["a", "b"]) != canonical_json(["b", "a"])


def test_hash_of_a_cleared_answer_is_none() -> None:
    assert hash_answer(None) is None


def test_equivalent_multi_select_answers_hash_identically() -> None:
    question = multi_select_question(1)
    ids = [option.option_id for option in question.options[:3]]

    forward = validate_answer(question, {"selectedOptionIds": ids})
    reverse = validate_answer(question, {"selectedOptionIds": list(reversed(ids))})

    # This is precisely what makes a repeated autosave a no-op.
    assert hash_answer(forward.canonical) == hash_answer(reverse.canonical)


def test_different_answers_hash_differently() -> None:
    question = single_choice_question(1)
    first = validate_answer(question, {"selectedOptionId": question.options[0].option_id})
    second = validate_answer(question, {"selectedOptionId": question.options[1].option_id})
    assert hash_answer(first.canonical) != hash_answer(second.canonical)


# ---------------------------------------------------------------------------
# Validation edge cases
# ---------------------------------------------------------------------------


def test_none_clears_any_answer() -> None:
    for builder in (
        single_choice_question,
        true_false_question,
        multi_select_question,
        drag_to_order_question,
        scenario_question,
    ):
        result = validate_answer(builder(1), None)
        assert result.answered is False
        assert result.complete is False
        assert result.canonical is None


def test_true_false_rejects_integers_masquerading_as_booleans() -> None:
    with pytest.raises(AppError) as caught:
        validate_answer(true_false_question(1), {"value": 1})
    assert caught.value.code is ErrorCode.INVALID_ANSWER


def test_multi_select_rejects_non_string_identifiers() -> None:
    question = multi_select_question(1)
    with pytest.raises(AppError):
        validate_answer(question, {"selectedOptionIds": [1, 2]})


def test_answer_must_be_an_object_not_a_bare_value() -> None:
    with pytest.raises(AppError):
        validate_answer(single_choice_question(1), "option-1")
    with pytest.raises(AppError):
        validate_answer(single_choice_question(1), ["option-1"])


def test_scenario_requires_an_object_with_responses() -> None:
    with pytest.raises(AppError):
        validate_answer(scenario_question(1), {"answers": []})


def test_scenario_with_an_empty_response_list_is_cleared() -> None:
    result = validate_answer(scenario_question(1), {"responses": []})
    assert result.answered is False


def test_scenario_completeness_requires_every_sub_question() -> None:
    question = scenario_question(1)
    subs = question.sub_questions

    partial = validate_answer(
        question,
        {
            "responses": [
                {
                    "subQuestionId": subs[0].sub_question_id,
                    "answer": {"selectedOptionId": subs[0].options[0].option_id},
                }
            ]
        },
    )
    assert partial.answered is True
    assert partial.complete is False

    full = validate_answer(
        question,
        {
            "responses": [
                {
                    "subQuestionId": subs[0].sub_question_id,
                    "answer": {"selectedOptionId": subs[0].options[0].option_id},
                },
                {"subQuestionId": subs[1].sub_question_id, "answer": {"value": True}},
                {
                    "subQuestionId": subs[2].sub_question_id,
                    "answer": {"selectedOptionIds": [subs[2].options[0].option_id]},
                },
            ]
        },
    )
    assert full.complete is True


def test_scenario_canonical_form_is_order_independent() -> None:
    question = scenario_question(1)
    subs = question.sub_questions
    a = {"subQuestionId": subs[1].sub_question_id, "answer": {"value": True}}
    b = {
        "subQuestionId": subs[0].sub_question_id,
        "answer": {"selectedOptionId": subs[0].options[0].option_id},
    }

    first = validate_answer(question, {"responses": [a, b]})
    second = validate_answer(question, {"responses": [b, a]})
    assert hash_answer(first.canonical) == hash_answer(second.canonical)


def test_drag_to_order_accepts_a_full_permutation() -> None:
    question = drag_to_order_question(1)
    ids = [item.item_id for item in question.order_items]
    result = validate_answer(question, {"orderedItemIds": list(reversed(ids))})
    assert result.complete is True
    assert result.canonical is not None
    assert result.canonical["orderedItemIds"] == list(reversed(ids))


# ---------------------------------------------------------------------------
# Deliverability
# ---------------------------------------------------------------------------


def test_a_choice_question_without_options_is_not_deliverable() -> None:
    broken = BankQuestion(
        question_id="q-broken",
        version=1,
        type=QuestionType.SINGLE_CHOICE,
        prompt="No options were authored.",
    )
    assert is_deliverable(broken) is False


def test_a_retired_question_is_not_deliverable() -> None:
    assert is_deliverable(single_choice_question(1, retired=True)) is False


def test_true_false_needs_no_options() -> None:
    assert is_deliverable(true_false_question(1)) is True


def test_a_scenario_with_a_broken_sub_question_is_not_deliverable() -> None:
    from app.modules.attempt_delivery.integration.uc02.types import ScenarioSubQuestion

    broken = BankQuestion(
        question_id="q-broken-scenario",
        version=1,
        type=QuestionType.SCENARIO,
        prompt="Scenario",
        sub_questions=(
            ScenarioSubQuestion(
                sub_question_id="s1",
                type=QuestionType.SINGLE_CHOICE,
                prompt="No options",
                options=(QuestionOption("only-one", "Just one option"),),
            ),
        ),
    )
    assert is_deliverable(broken) is False


# ---------------------------------------------------------------------------
# Seeded randomisation
# ---------------------------------------------------------------------------


def test_the_same_seed_reproduces_the_same_shuffle() -> None:
    items = list(range(20))
    assert shuffled(items, make_rng("attempt-1")) == shuffled(items, make_rng("attempt-1"))


def test_different_seeds_produce_different_shuffles() -> None:
    items = list(range(20))
    assert shuffled(items, make_rng("attempt-1")) != shuffled(items, make_rng("attempt-2"))


def test_shuffling_does_not_mutate_the_input() -> None:
    items = [1, 2, 3, 4, 5]
    shuffled(items, make_rng("seed"))
    assert items == [1, 2, 3, 4, 5]


def test_sampling_returns_the_requested_count() -> None:
    items = list(range(50))
    drawn = sample_without_replacement(items, 10, make_rng("seed"))
    assert len(drawn) == 10
    assert len(set(drawn)) == 10


def test_sampling_more_than_available_returns_everything() -> None:
    items = list(range(5))
    drawn = sample_without_replacement(items, 10, make_rng("seed"))
    assert sorted(drawn) == items


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def test_iso_rendering_uses_a_z_suffix() -> None:
    assert to_iso(datetime(2026, 3, 1, 9, 0, tzinfo=UTC)) == "2026-03-01T09:00:00Z"


def test_parsing_accepts_both_z_and_offset_forms() -> None:
    assert parse_instant("2026-03-01T09:00:00Z") == parse_instant("2026-03-01T09:00:00+00:00")


def test_parsing_normalises_a_non_utc_offset() -> None:
    assert parse_instant("2026-03-01T11:00:00+02:00") == datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_instant("2026-03-01T09:00:00")
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 3, 1, 9, 0))


def test_the_fixed_clock_only_moves_when_told() -> None:
    clock = FixedClock("2026-03-01T09:00:00Z")
    start = clock.now()
    assert clock.now() == start

    clock.advance(minutes=5)
    assert clock.now() == start + timedelta(minutes=5)

    with pytest.raises(ValueError):
        clock.advance(seconds=-1)


# ---------------------------------------------------------------------------
# Configuration locking
# ---------------------------------------------------------------------------


def _configuration(**overrides: object) -> QuizConfigurationVersion:
    base: dict[str, object] = {
        "configuration_version_id": "cfg-1",
        "quiz_id": "quiz-1",
        "course_id": "course-1",
        "version": 1,
        "question_count": 5,
        "pass_mark_percentage": 70,
        "activated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return QuizConfigurationVersion(**base)  # type: ignore[arg-type]


def test_locking_returns_a_fully_specified_snapshot() -> None:
    snapshot = lock_configuration(_configuration())
    assert snapshot.question_presentation is QuestionPresentation.ALL_AT_ONCE
    assert snapshot.allow_incomplete_submission is True
    assert snapshot.randomise_question_order is False
    assert snapshot.time_limit_seconds is None
    assert snapshot.max_attempts is None


def test_locking_rejects_a_missing_identifier() -> None:
    with pytest.raises(AppError) as caught:
        lock_configuration(_configuration(configuration_version_id=""))
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_locking_rejects_a_zero_time_limit() -> None:
    with pytest.raises(AppError):
        lock_configuration(_configuration(time_limit_seconds=0))


def test_locking_rejects_a_zero_max_attempts() -> None:
    with pytest.raises(AppError):
        lock_configuration(_configuration(max_attempts=0))


def test_snapshot_round_trips_through_json() -> None:
    from app.modules.attempt_delivery.integration.uc01.types import QuestionTypeQuota

    original = lock_configuration(
        _configuration(
            question_count=4,
            time_limit_seconds=600,
            question_type_quotas=(
                QuestionTypeQuota(QuestionType.SINGLE_CHOICE, 3),
                QuestionTypeQuota(QuestionType.TRUE_FALSE, 1),
            ),
            question_presentation=QuestionPresentation.ONE_AT_A_TIME,
            randomise_question_order=True,
        )
    )

    # The attempt stores the snapshot as JSON, so a lossy round trip would silently
    # change an attempt's rules.
    restored = QuizConfigurationVersion.from_dict(original.to_dict())
    assert restored == original
