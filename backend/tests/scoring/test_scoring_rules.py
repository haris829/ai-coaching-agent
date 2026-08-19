"""UC-04's scoring rules, tested as pure functions.

No database, no HTTP, no clock: each case states a delivered question, an answer key and a response,
then asserts the marks. That is what makes it worth enumerating the boundaries — an empty selection,
a deduction that would take a question negative, an ordering that is one swap away from right.

The five rules under test:

* SINGLE_CHOICE / TRUE_FALSE — correct is full marks, anything else is zero
* MULTI_SELECT — the configured marks and deductions, floored at zero for the question
* SCENARIO — only the configured primary answer is scored
* DRAG_TO_ORDER — the exact sequence, with no partial credit
* every type — an unanswered question scores zero
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.scoring.domain.answer_key import (
    KeyOption,
    MarkingPolicy,
    derive_answer_key,
)
from app.modules.scoring.domain.enums import (
    AnswerKeySource,
    QuestionOutcome,
    QuestionType,
    ScoreAnomaly,
)
from app.modules.scoring.domain.scoring import aggregate, score_question
from tests.support.results_world import answer_key, delivered, option, order_item


def _four_options() -> list:
    return [
        option("A", "Raise the alarm", correct=True),
        option("B", "Collect belongings"),
        option("C", "Finish the task"),
        option("D", "Ask a colleague"),
    ]


def _single_choice(response: dict | None, *, points: float = 2.0):
    question = delivered(
        QuestionType.SINGLE_CHOICE, options=_four_options(), response=response, points=points
    )
    return question, answer_key(question, correct_ids=["A"])


def _true_false(response: dict | None, *, points: float = 1.0, correct: str = "TRUE"):
    question = delivered(
        QuestionType.TRUE_FALSE,
        options=[
            option("TRUE", "True", correct=correct == "TRUE"),
            option("FALSE", "False", correct=correct == "FALSE"),
        ],
        response=response,
        points=points,
    )
    return question, answer_key(question, correct_ids=[correct])


def _multi_select(
    response: dict | None,
    *,
    points: float = 4.0,
    policy: MarkingPolicy = MarkingPolicy.PARTIAL_WITH_DEDUCTION,
    deduction: float = 1.0,
):
    question = delivered(
        QuestionType.MULTI_SELECT,
        options=[
            option("A", "Close doors behind you", correct=True),
            option("B", "Use the nearest exit", correct=True),
            option("C", "Use the lift"),
            option("D", "Return for belongings"),
        ],
        response=response,
        points=points,
    )
    return question, answer_key(
        question, correct_ids=["A", "B"], policy=policy, deduction=deduction
    )


def _scenario(response: dict | None, *, points: float = 2.0, primary: str | None = "A"):
    question = delivered(
        QuestionType.SCENARIO,
        question_id="q-scenario-1",
        options=[
            option("A", "Evacuate and report", correct=True),
            option("B", "Investigate the smoke", correct=True),
            option("C", "Wait for instructions"),
        ],
        sub_question_ids=["q-scenario-1:1"],
        scenario_text="Smoke is coming from a socket while you are working alone.",
        response=response,
        points=points,
    )
    return question, answer_key(question, correct_ids=["A", "B"], primary_id=primary)


def _drag_to_order(response: dict | None, *, points: float = 3.0, policy=MarkingPolicy.EXACT):
    question = delivered(
        QuestionType.DRAG_TO_ORDER,
        # Presented out of sequence on purpose: the key is `correct_position`, never the order
        # shown.
        options=[
            order_item("S3", "Report to the assembly point", position=3),
            order_item("S1", "Raise the alarm", position=1),
            order_item("S2", "Evacuate the area", position=2),
        ],
        response=response,
        points=points,
    )
    return question, answer_key(question, correct_order=["S1", "S2", "S3"], policy=policy)


def _scenario_answer(option_id: str) -> dict:
    return {
        "type": "SCENARIO",
        "responses": [
            {
                "subQuestionId": "q-scenario-1:1",
                "answer": {"type": "SINGLE_CHOICE", "selectedOptionId": option_id},
            }
        ],
    }


# ---------------------------------------------------------------------------
# Single choice and True/False
# ---------------------------------------------------------------------------


class TestSingleChoice:
    def test_the_correct_option_earns_the_full_marks(self) -> None:
        question, key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        score = score_question(question, key)
        assert (score.awarded_marks, score.maximum_marks) == (2.0, 2.0)
        assert score.outcome is QuestionOutcome.CORRECT

    def test_a_wrong_option_earns_nothing(self) -> None:
        question, key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "B"})
        score = score_question(question, key)
        assert score.awarded_marks == 0.0
        assert score.outcome is QuestionOutcome.INCORRECT

    def test_the_correct_answer_is_reported_even_when_the_learner_was_wrong(self) -> None:
        question, key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "B"})
        score = score_question(question, key)
        assert score.correct_answer_display["optionIds"] == ["A"]
        assert score.correct_answer_display["labels"] == ["Raise the alarm"]
        assert score.learner_answer_display["optionIds"] == ["B"]

    def test_every_option_is_reported_with_its_correctness(self) -> None:
        question, key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        score = score_question(question, key)
        assert [(mark.option_id, mark.correct, mark.selected) for mark in score.option_marks] == [
            ("A", True, True),
            ("B", False, False),
            ("C", False, False),
            ("D", False, False),
        ]


class TestTrueFalse:
    def test_the_correct_boolean_earns_the_full_marks(self) -> None:
        question, key = _true_false({"type": "TRUE_FALSE", "value": True})
        assert score_question(question, key).awarded_marks == 1.0

    def test_the_wrong_boolean_earns_nothing(self) -> None:
        question, key = _true_false({"type": "TRUE_FALSE", "value": False})
        score = score_question(question, key)
        assert score.awarded_marks == 0.0
        assert score.outcome is QuestionOutcome.INCORRECT

    def test_false_is_marked_correct_when_false_is_the_answer(self) -> None:
        question, key = _true_false({"type": "TRUE_FALSE", "value": False}, correct="FALSE")
        assert score_question(question, key).awarded_marks == 1.0

    def test_the_boolean_maps_to_the_labelled_option_not_to_a_position(self) -> None:
        """A shuffled presentation must not be able to invert the marking."""
        question, key = _true_false({"type": "TRUE_FALSE", "value": True})
        reversed_key = replace(key, options=tuple(reversed(key.options)))
        assert score_question(question, reversed_key).awarded_marks == 1.0

    def test_a_plain_option_selection_is_accepted_too(self) -> None:
        """Some clients send the chosen option rather than the boolean; both mean the same thing."""
        question, key = _true_false({"type": "SINGLE_CHOICE", "selectedOptionId": "TRUE"})
        assert score_question(question, key).awarded_marks == 1.0


# ---------------------------------------------------------------------------
# Multi-select
# ---------------------------------------------------------------------------


class TestMultiSelect:
    def test_every_correct_option_and_nothing_else_earns_full_marks(self) -> None:
        question, key = _multi_select({"type": "MULTI_SELECT", "selectedOptionIds": ["A", "B"]})
        score = score_question(question, key)
        assert score.awarded_marks == 4.0
        assert score.outcome is QuestionOutcome.CORRECT

    def test_a_partial_selection_earns_pro_rata_marks(self) -> None:
        question, key = _multi_select({"type": "MULTI_SELECT", "selectedOptionIds": ["A"]})
        score = score_question(question, key)
        assert score.awarded_marks == 2.0
        assert score.outcome is QuestionOutcome.PARTIALLY_CORRECT

    def test_an_incorrect_selection_is_deducted(self) -> None:
        question, key = _multi_select(
            {"type": "MULTI_SELECT", "selectedOptionIds": ["A", "B", "C"]}, deduction=1.0
        )
        score = score_question(question, key)
        # Both correct options (4) minus one incorrect selection (1).
        assert (score.raw_marks, score.awarded_marks, score.deduction) == (3.0, 3.0, 1.0)

    def test_deductions_never_take_a_question_below_zero(self) -> None:
        question, key = _multi_select(
            {"type": "MULTI_SELECT", "selectedOptionIds": ["A", "C", "D"]}, deduction=3.0
        )
        score = score_question(question, key)
        assert score.raw_marks < 0
        assert score.awarded_marks == 0.0
        assert score.outcome is QuestionOutcome.INCORRECT

    def test_each_option_reports_what_it_contributed(self) -> None:
        question, key = _multi_select(
            {"type": "MULTI_SELECT", "selectedOptionIds": ["A", "C"]}, deduction=1.0
        )
        contributions = {
            mark.option_id: mark.mark_contribution
            for mark in score_question(question, key).option_marks
        }
        assert contributions["A"] == 2.0
        assert contributions["C"] == -1.0
        # An option the learner did not touch contributed nothing either way.
        assert contributions["B"] == 0.0

    def test_an_all_or_nothing_multi_select_gives_no_partial_credit(self) -> None:
        partial, key = _multi_select(
            {"type": "MULTI_SELECT", "selectedOptionIds": ["A"]},
            policy=MarkingPolicy.EXACT,
            deduction=0.0,
        )
        assert score_question(partial, key).awarded_marks == 0.0

        whole, key = _multi_select(
            {"type": "MULTI_SELECT", "selectedOptionIds": ["A", "B"]},
            policy=MarkingPolicy.EXACT,
            deduction=0.0,
        )
        assert score_question(whole, key).awarded_marks == 4.0

    def test_partial_credit_without_penalty_ignores_the_deduction(self) -> None:
        question, key = _multi_select(
            {"type": "MULTI_SELECT", "selectedOptionIds": ["A", "C"]},
            policy=MarkingPolicy.PARTIAL,
            deduction=5.0,
        )
        score = score_question(question, key)
        assert (score.awarded_marks, score.deduction) == (2.0, 0.0)

    def test_selecting_everything_cannot_exceed_the_maximum(self) -> None:
        question, key = _multi_select(
            {"type": "MULTI_SELECT", "selectedOptionIds": ["A", "B", "C", "D"]},
            policy=MarkingPolicy.PARTIAL,
            deduction=0.0,
        )
        score = score_question(question, key)
        assert score.awarded_marks == 4.0
        # ...and it is not "correct": the set does not match the key.
        assert score.outcome is QuestionOutcome.PARTIALLY_CORRECT


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


class TestScenario:
    def test_the_primary_answer_earns_the_full_marks(self) -> None:
        question, key = _scenario(_scenario_answer("A"))
        score = score_question(question, key)
        assert score.awarded_marks == 2.0
        assert score.outcome is QuestionOutcome.CORRECT

    def test_another_correct_option_that_is_not_the_primary_earns_nothing(self) -> None:
        """The key marks B correct and A primary. Only the configured primary answer is scored."""
        question, key = _scenario(_scenario_answer("B"))
        assert "B" in key.correct_option_ids
        score = score_question(question, key)
        assert score.awarded_marks == 0.0
        assert score.correct_answer_display["optionIds"] == ["A"]

    def test_a_scenario_with_no_single_primary_answer_is_an_anomaly(self) -> None:
        """Two correct options and no primary flag makes "score the primary answer" undefined."""
        question, key = _scenario(_scenario_answer("A"), primary=None)
        score = score_question(question, key)
        assert score.anomaly is ScoreAnomaly.AMBIGUOUS_PRIMARY_ANSWER
        assert score.awarded_marks == 0.0

    def test_a_single_correct_option_is_treated_as_the_primary_answer(self) -> None:
        """Mirrors the convenience UC-02's own validator applies when authoring a scenario."""
        question = delivered(
            QuestionType.SCENARIO,
            question_id="q-scenario-1",
            options=[option("A", "Evacuate", correct=True), option("B", "Wait")],
            sub_question_ids=["q-scenario-1:1"],
            response=_scenario_answer("A"),
            points=2.0,
        )
        key = answer_key(question, correct_ids=["A"])
        assert key.primary_option_id == "A"
        assert score_question(question, key).awarded_marks == 2.0

    def test_a_scenario_left_unanswered_scores_zero_without_being_an_anomaly(self) -> None:
        question, key = _scenario(None)
        score = score_question(question, key)
        assert score.outcome is QuestionOutcome.UNANSWERED
        assert score.anomaly is None


# ---------------------------------------------------------------------------
# Drag to order
# ---------------------------------------------------------------------------


class TestDragToOrder:
    def test_the_exact_sequence_earns_the_full_marks(self) -> None:
        question, key = _drag_to_order(
            {"type": "DRAG_TO_ORDER", "orderedItemIds": ["S1", "S2", "S3"]}
        )
        score = score_question(question, key)
        assert score.awarded_marks == 3.0
        assert score.outcome is QuestionOutcome.CORRECT

    def test_one_swap_earns_nothing_at_all(self) -> None:
        question, key = _drag_to_order(
            {"type": "DRAG_TO_ORDER", "orderedItemIds": ["S2", "S1", "S3"]}
        )
        score = score_question(question, key)
        assert score.awarded_marks == 0.0
        assert score.outcome is QuestionOutcome.INCORRECT

    def test_two_of_three_correct_positions_still_earn_nothing(self) -> None:
        question, key = _drag_to_order(
            {"type": "DRAG_TO_ORDER", "orderedItemIds": ["S1", "S3", "S2"]}
        )
        score = score_question(question, key)
        assert score.awarded_marks == 0.0
        # The per-position detail still shows what was right, for the feedback report.
        assert [mark.correct for mark in score.option_marks] == [True, False, False]

    def test_the_presented_order_is_irrelevant_to_the_marking(self) -> None:
        question, key = _drag_to_order(
            {"type": "DRAG_TO_ORDER", "orderedItemIds": ["S1", "S2", "S3"]}
        )
        assert [item.option_id for item in question.options] == ["S3", "S1", "S2"]
        assert score_question(question, key).awarded_marks == 3.0
        assert score_question(question, key).correct_answer_display["orderedItemIds"] == [
            "S1",
            "S2",
            "S3",
        ]

    def test_a_partial_credit_strategy_cannot_soften_the_rule(self) -> None:
        """UC-02's own grader would give partial credit here. UC-04's rule is the quiz's rule."""
        question, key = _drag_to_order(
            {"type": "DRAG_TO_ORDER", "orderedItemIds": ["S1", "S3", "S2"]},
            policy=MarkingPolicy.PARTIAL,
        )
        assert score_question(question, key).awarded_marks == 0.0


# ---------------------------------------------------------------------------
# Unanswered, missing keys and unusable data
# ---------------------------------------------------------------------------

_EVERY_TYPE = [
    ("single choice", _single_choice),
    ("true/false", _true_false),
    ("multi-select", _multi_select),
    ("scenario", _scenario),
    ("drag to order", _drag_to_order),
]


class TestZeroAndAnomalies:
    @pytest.mark.parametrize("label,builder", _EVERY_TYPE, ids=[item[0] for item in _EVERY_TYPE])
    def test_an_unanswered_question_of_any_type_scores_zero(self, label: str, builder) -> None:
        question, key = builder(None)
        score = score_question(question, key)
        assert score.awarded_marks == 0.0
        assert score.outcome is QuestionOutcome.UNANSWERED
        assert score.answered is False
        # Not an anomaly: an unanswered question is a normal outcome, not a data defect.
        assert score.anomaly is None

    @pytest.mark.parametrize("label,builder", _EVERY_TYPE, ids=[item[0] for item in _EVERY_TYPE])
    def test_the_maximum_marks_still_count_for_an_unanswered_question(
        self, label: str, builder
    ) -> None:
        question, key = builder(None, points=5.0)
        assert score_question(question, key).maximum_marks == 5.0

    def test_a_question_with_no_answer_key_is_reported_not_scored_as_zero(self) -> None:
        """A zero nobody earned is indistinguishable from a wrong answer once it is stored."""
        question, _key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        score = score_question(question, None)
        assert score.anomaly is ScoreAnomaly.MISSING_ANSWER_KEY
        assert score.outcome is QuestionOutcome.NOT_SCORED
        assert score.awarded_marks == 0.0

    def test_an_answer_key_with_no_correct_option_is_unusable(self) -> None:
        question, key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        keyless = replace(
            key, options=tuple(replace(item, is_correct=False) for item in key.options)
        )
        assert keyless.is_usable() is False
        assert score_question(question, keyless).anomaly is ScoreAnomaly.MISSING_ANSWER_KEY

    def test_a_drag_to_order_key_missing_a_position_is_unusable(self) -> None:
        question, key = _drag_to_order(
            {"type": "DRAG_TO_ORDER", "orderedItemIds": ["S1", "S2", "S3"]}
        )
        partial = replace(
            key,
            options=(
                KeyOption("S1", "Raise the alarm", correct_position=1),
                KeyOption("S2", "Evacuate the area", correct_position=2),
                KeyOption("S3", "Report to the assembly point"),
            ),
        )
        assert partial.is_usable() is False

    def test_an_answer_of_the_wrong_shape_is_reported_rather_than_crashing(self) -> None:
        question, key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionIds": ["A"]})
        score = score_question(question, key)
        assert score.anomaly is ScoreAnomaly.UNREADABLE_ANSWER
        assert score.awarded_marks == 0.0

    def test_a_delivered_type_that_disagrees_with_the_key_is_an_anomaly(self) -> None:
        question, key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        mismatched = replace(key, question_type=QuestionType.MULTI_SELECT)
        assert (
            score_question(question, mismatched).anomaly is ScoreAnomaly.UNSUPPORTED_QUESTION_TYPE
        )


class TestTheFallbackKey:
    """Scoring from the answer key UC-03 froze onto the attempt."""

    def test_the_frozen_copy_marks_a_question_the_same_way(self) -> None:
        question, _key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        derived = derive_answer_key(question)
        assert derived.source is AnswerKeySource.ATTEMPT_SNAPSHOT
        assert score_question(question, derived).awarded_marks == 2.0

    def test_the_frozen_copy_preserves_a_drag_to_order_key(self) -> None:
        question, _key = _drag_to_order(
            {"type": "DRAG_TO_ORDER", "orderedItemIds": ["S1", "S2", "S3"]}
        )
        derived = derive_answer_key(question)
        assert derived.correct_order == ("S1", "S2", "S3")
        assert score_question(question, derived).awarded_marks == 3.0

    def test_the_frozen_copy_defaults_to_no_partial_credit(self) -> None:
        """The conservative direction: a policy nobody passed cannot award marks nobody
        configured."""
        question, _key = _multi_select({"type": "MULTI_SELECT", "selectedOptionIds": ["A"]})
        derived = derive_answer_key(question)
        assert derived.marking_policy is MarkingPolicy.EXACT
        assert score_question(question, derived).awarded_marks == 0.0

    def test_the_configured_policy_can_be_carried_into_the_frozen_copy(self) -> None:
        question, _key = _multi_select({"type": "MULTI_SELECT", "selectedOptionIds": ["A", "C"]})
        derived = derive_answer_key(
            question,
            marking_policy=MarkingPolicy.PARTIAL_WITH_DEDUCTION,
            deduction_per_incorrect=1.0,
        )
        assert score_question(question, derived).awarded_marks == 1.0

    def test_the_marks_frozen_on_the_attempt_win_over_the_banks_current_value(self) -> None:
        """The attempt is authoritative for what its own questions were worth."""
        question, key = _single_choice(
            {"type": "SINGLE_CHOICE", "selectedOptionId": "A"}, points=2.0
        )
        repriced = replace(key, max_marks=99.0)
        score = score_question(question, repriced)
        assert (score.awarded_marks, score.maximum_marks) == (2.0, 2.0)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_totals_and_percentage_come_from_the_marks_not_the_question_count(self) -> None:
        right, right_key = _single_choice(
            {"type": "SINGLE_CHOICE", "selectedOptionId": "A"}, points=1.0
        )
        blank, blank_key = _drag_to_order(None, points=3.0)
        totals = aggregate([score_question(right, right_key), score_question(blank, blank_key)])
        assert (totals.total_marks, totals.maximum_marks) == (1.0, 4.0)
        assert totals.percentage == 25.0

    def test_the_percentage_is_rounded_to_two_decimals(self) -> None:
        right, right_key = _single_choice(
            {"type": "SINGLE_CHOICE", "selectedOptionId": "A"}, points=1.0
        )
        wrong, wrong_key = _single_choice(
            {"type": "SINGLE_CHOICE", "selectedOptionId": "B"}, points=1.0
        )
        blank, blank_key = _single_choice(None, points=1.0)
        totals = aggregate(
            [
                score_question(right, right_key),
                score_question(wrong, wrong_key),
                score_question(blank, blank_key),
            ]
        )
        assert totals.percentage == 33.33

    def test_the_counts_separate_correct_incorrect_and_unanswered(self) -> None:
        right, right_key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        wrong, wrong_key = _true_false({"type": "TRUE_FALSE", "value": False})
        blank, blank_key = _scenario(None)
        totals = aggregate(
            [
                score_question(right, right_key),
                score_question(wrong, wrong_key),
                score_question(blank, blank_key),
            ]
        )
        assert (totals.correct_count, totals.incorrect_count, totals.unanswered_count) == (1, 1, 1)
        assert totals.total_questions == 3

    def test_a_partially_correct_question_counts_as_incorrect_and_is_reported_separately(
        self,
    ) -> None:
        question, key = _multi_select({"type": "MULTI_SELECT", "selectedOptionIds": ["A"]})
        totals = aggregate([score_question(question, key)])
        assert totals.incorrect_count == 1
        assert totals.partially_correct_count == 1

    def test_a_zero_maximum_is_an_anomaly_rather_than_a_division_by_zero(self) -> None:
        question, key = _single_choice(
            {"type": "SINGLE_CHOICE", "selectedOptionId": "A"}, points=0.0
        )
        totals = aggregate([score_question(question, key)])
        assert totals.percentage == 0.0
        assert [item["code"] for item in totals.anomalies] == [str(ScoreAnomaly.ZERO_MAXIMUM_MARKS)]
        assert totals.confirmable is False

    def test_an_attempt_with_no_questions_is_an_anomaly(self) -> None:
        totals = aggregate([])
        assert [item["code"] for item in totals.anomalies] == [
            str(ScoreAnomaly.NO_QUESTIONS_DELIVERED)
        ]
        assert totals.confirmable is False

    def test_a_per_question_anomaly_blocks_confirmation(self) -> None:
        question, _key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        totals = aggregate([score_question(question, None)])
        assert totals.confirmable is False
        assert totals.anomalies[0]["code"] == str(ScoreAnomaly.MISSING_ANSWER_KEY)
        assert totals.anomalies[0]["questionId"] == question.question_id

    def test_a_clean_attempt_is_confirmable(self) -> None:
        question, key = _single_choice({"type": "SINGLE_CHOICE", "selectedOptionId": "A"})
        assert aggregate([score_question(question, key)]).confirmable is True
