"""Exclusion-aware question planning and the difference rule (§5, §6, §7, §8).

The planner is a pure function, so the small-bank behaviour §8 describes can be asserted directly
rather than inferred from what a selector happened to return. The scenario §8 gives —
``configured 10, previously used 8, alternatives 5`` — is a test here, by name.
"""

from __future__ import annotations

import pytest

from app.modules.retakes.domain.difference import compare_question_sets
from app.modules.retakes.domain.enums import ExclusionScope, QuestionReuseReason
from app.modules.retakes.domain.question_plan import plan_retake_questions
from app.modules.retakes.integration.uc01 import QuestionTypeQuota, QuizConfigurationVersion
from app.modules.retakes.integration.uc02 import QuestionDescriptor


def _config(
    *,
    question_count: int = 3,
    quotas: tuple[tuple[str, int], ...] = (),
    allowed: tuple[str, ...] = (),
) -> QuizConfigurationVersion:
    return QuizConfigurationVersion(
        configuration_version_id="cfg-v1",
        quiz_id="quiz-1",
        course_id="course-1",
        version=1,
        question_count=question_count,
        maximum_attempts=3,
        question_type_quotas=tuple(
            QuestionTypeQuota(type=name, count=count) for name, count in quotas
        ),
        allowed_question_types=allowed,
    )


def _pool(*specs: tuple[str, str] | str, retired: tuple[str, ...] = ()) -> tuple[QuestionDescriptor, ...]:
    questions = []
    for spec in specs:
        question_id, question_type = spec if isinstance(spec, tuple) else (spec, "SINGLE_CHOICE")
        questions.append(
            QuestionDescriptor(
                question_id=question_id,
                question_type=question_type,
                retired=question_id in retired,
            )
        )
    return tuple(questions)


# ---------------------------------------------------------------------------
# The preferred outcome: exclude the whole history
# ---------------------------------------------------------------------------


def test_whole_history_is_excluded_when_the_bank_can_support_it():
    plan = plan_retake_questions(
        config=_config(question_count=3),
        pool=_pool("q1", "q2", "q3", "q4", "q5", "q6", "q7"),
        previous_attempt_question_ids=("q1", "q2", "q3"),
        historical_question_ids=("q1", "q2", "q3", "q4"),
    )

    assert plan.exclusion_scope is ExclusionScope.ALL_PREVIOUS_ATTEMPTS
    assert set(plan.excluded_question_ids) == {"q1", "q2", "q3", "q4"}
    assert plan.reuse_expected is False
    assert plan.reuse_reason is None
    # Every question can be new relative to the paper being retaken.
    assert plan.expected_fresh_questions == 3


def test_falls_back_to_the_previous_attempt_only_when_history_is_too_wide():
    """Excluding q1–q5 would leave two questions for a three-question paper.

    So the plan degrades one step: the paper being retaken is still avoided in full, which is what
    §7 actually requires, and a question from an older attempt may return.
    """
    plan = plan_retake_questions(
        config=_config(question_count=3),
        pool=_pool("q1", "q2", "q3", "q4", "q5", "q6", "q7"),
        previous_attempt_question_ids=("q1", "q2", "q3"),
        historical_question_ids=("q1", "q2", "q3", "q4", "q5"),
    )

    assert plan.exclusion_scope is ExclusionScope.PREVIOUS_ATTEMPT_ONLY
    assert set(plan.excluded_question_ids) == {"q1", "q2", "q3"}
    assert plan.reuse_expected is True
    assert plan.reuse_reason is QuestionReuseReason.INSUFFICIENT_UNUSED_QUESTIONS
    assert plan.expected_fresh_questions == 3


# ---------------------------------------------------------------------------
# §8 — the small question bank
# ---------------------------------------------------------------------------


def test_section_8_scenario_configured_10_used_8_alternatives_5():
    """The example §8 gives, with the numbers it gives.

    Ten questions are required, thirteen exist, and only five are unused. The paper is still filled
    — not failed — and the reuse is recorded with a reason.
    """
    pool = _pool(*[f"q{index}" for index in range(1, 14)])
    previous = tuple(f"q{index}" for index in range(1, 9))

    plan = plan_retake_questions(
        config=_config(question_count=10),
        pool=pool,
        previous_attempt_question_ids=previous,
        historical_question_ids=previous,
    )

    assert plan.feasible is True
    assert plan.exclusion_scope is ExclusionScope.NONE
    assert plan.reuse_expected is True
    assert plan.reuse_reason is QuestionReuseReason.INSUFFICIENT_UNUSED_QUESTIONS
    assert plan.unused_pool_size == 5
    # Five of the ten delivered questions can be new; the other five must repeat.
    assert plan.expected_fresh_questions == 5


def test_an_identical_bank_reports_reuse_as_unavoidable():
    """Bank of exactly three, paper of three: the same paper is the only paper."""
    plan = plan_retake_questions(
        config=_config(question_count=3),
        pool=_pool("q1", "q2", "q3"),
        previous_attempt_question_ids=("q1", "q2", "q3"),
    )

    assert plan.feasible is True
    assert plan.exclusion_scope is ExclusionScope.NONE
    assert plan.reuse_expected is True
    assert plan.expected_fresh_questions == 0


def test_an_undersized_bank_is_infeasible_rather_than_short():
    """A paper short of its configured count is not a valid paper, so this is a refusal."""
    plan = plan_retake_questions(
        config=_config(question_count=5),
        pool=_pool("q1", "q2", "q3"),
        previous_attempt_question_ids=("q1", "q2"),
    )

    assert plan.feasible is False
    assert plan.shortfalls == ({"type": "ANY", "required": 5, "available": 3},)


# ---------------------------------------------------------------------------
# Retired and ineligible questions
# ---------------------------------------------------------------------------


def test_retired_questions_are_never_reached_for_to_avoid_reuse():
    """§8: never select a retired question merely to avoid repeating one.

    q4 and q5 are retired, so the only alternatives are the questions already seen — and the plan
    reports reuse rather than quietly delivering a withdrawn question.
    """
    plan = plan_retake_questions(
        config=_config(question_count=3),
        pool=_pool("q1", "q2", "q3", "q4", "q5", retired=("q4", "q5")),
        previous_attempt_question_ids=("q1", "q2", "q3"),
    )

    assert plan.eligible_pool_size == 3
    assert plan.unused_pool_size == 0
    assert plan.reuse_expected is True
    assert plan.expected_fresh_questions == 0


def test_questions_outside_the_allowed_types_are_not_alternatives():
    plan = plan_retake_questions(
        config=_config(question_count=2, allowed=("TRUE_FALSE",)),
        pool=_pool(("t1", "TRUE_FALSE"), ("t2", "TRUE_FALSE"), ("s1", "SINGLE_CHOICE")),
        previous_attempt_question_ids=("t1", "t2"),
    )

    assert plan.eligible_pool_size == 2
    assert plan.reuse_expected is True


# ---------------------------------------------------------------------------
# Type quotas (§6, §8)
# ---------------------------------------------------------------------------


def test_a_type_quota_is_never_bent_to_avoid_reuse():
    """Surplus questions of one type cannot cover a shortfall in another.

    Four unused SINGLE_CHOICE questions exist, but the quota needs one SCENARIO and the only
    SCENARIO question has already been seen. So it is reused — and the reason names the type.
    """
    plan = plan_retake_questions(
        config=_config(question_count=3, quotas=(("SINGLE_CHOICE", 2), ("SCENARIO", 1))),
        pool=_pool(
            ("a1", "SINGLE_CHOICE"),
            ("a2", "SINGLE_CHOICE"),
            ("a3", "SINGLE_CHOICE"),
            ("a4", "SINGLE_CHOICE"),
            ("sc1", "SCENARIO"),
        ),
        previous_attempt_question_ids=("a1", "a2", "sc1"),
    )

    assert plan.reuse_expected is True
    assert plan.reuse_reason is QuestionReuseReason.INSUFFICIENT_UNUSED_QUESTIONS_OF_TYPE
    # Two of the three can be fresh: the two single-choice slots. The scenario must repeat.
    assert plan.expected_fresh_questions == 2


def test_quota_shortfall_names_the_type_that_caused_it():
    plan = plan_retake_questions(
        config=_config(question_count=3, quotas=(("SINGLE_CHOICE", 2), ("SCENARIO", 1))),
        pool=_pool(("a1", "SINGLE_CHOICE"), ("a2", "SINGLE_CHOICE")),
        previous_attempt_question_ids=(),
    )

    assert plan.feasible is False
    assert plan.shortfalls == ({"type": "SCENARIO", "required": 1, "available": 0},)


def test_type_availability_is_reported_per_quota():
    plan = plan_retake_questions(
        config=_config(question_count=2, quotas=(("SINGLE_CHOICE", 1), ("TRUE_FALSE", 1))),
        pool=_pool(
            ("a1", "SINGLE_CHOICE"),
            ("a2", "SINGLE_CHOICE"),
            ("t1", "TRUE_FALSE"),
            ("t2", "TRUE_FALSE"),
        ),
        previous_attempt_question_ids=("a1", "t1"),
    )

    rows = {row.type: row for row in plan.type_availability}
    assert rows["SINGLE_CHOICE"].eligible == 2
    assert rows["SINGLE_CHOICE"].unused == 1
    assert rows["TRUE_FALSE"].required == 1


# ---------------------------------------------------------------------------
# §7 — meaningful difference
# ---------------------------------------------------------------------------


def test_a_reordered_identical_paper_is_not_meaningfully_different():
    """The exact example §7 gives: Q1–Q5 delivered again as Q3 Q1 Q5 Q2 Q4."""
    difference = compare_question_sets(
        previous_question_ids=("q1", "q2", "q3", "q4", "q5"),
        retake_question_ids=("q3", "q1", "q5", "q2", "q4"),
        expected_fresh_questions=5,
    )

    assert difference.identical_question_set is True
    assert difference.new_question_count == 0
    assert difference.satisfied is False


def test_a_wholly_new_paper_satisfies_the_rule():
    difference = compare_question_sets(
        previous_question_ids=("q1", "q2", "q3"),
        retake_question_ids=("q4", "q5", "q6"),
        expected_fresh_questions=3,
    )

    assert difference.new_question_count == 3
    assert difference.identical_question_set is False
    assert difference.satisfied is True
    assert difference.reuse_unavoidable is False


def test_partial_overlap_passes_when_that_was_all_the_bank_allowed():
    """Two new questions out of three, and two was the maximum possible. Not a defect."""
    difference = compare_question_sets(
        previous_question_ids=("q1", "q2", "q3"),
        retake_question_ids=("q4", "q5", "q1"),
        expected_fresh_questions=2,
    )

    assert difference.new_question_count == 2
    assert difference.repeated_question_count == 1
    assert difference.satisfied is True
    assert difference.reuse_unavoidable is True


def test_partial_overlap_fails_when_more_was_available():
    difference = compare_question_sets(
        previous_question_ids=("q1", "q2", "q3"),
        retake_question_ids=("q4", "q5", "q1"),
        expected_fresh_questions=3,
    )

    assert difference.satisfied is False
    assert difference.repeated_question_ids == ("q1",)


def test_an_unavoidably_identical_paper_is_not_reported_as_a_defect():
    """Bank of three, paper of three. Nothing better was possible, so nothing is flagged."""
    difference = compare_question_sets(
        previous_question_ids=("q1", "q2", "q3"),
        retake_question_ids=("q1", "q2", "q3"),
        expected_fresh_questions=0,
    )

    assert difference.identical_question_set is True
    assert difference.satisfied is True
    assert difference.reuse_unavoidable is True


def test_unseen_count_is_measured_against_the_whole_history():
    """"New" and "never seen" differ once there are three attempts, and both are reported."""
    difference = compare_question_sets(
        previous_question_ids=("q3", "q4"),
        retake_question_ids=("q1", "q5"),
        expected_fresh_questions=2,
        historical_question_ids=("q1", "q2", "q3", "q4"),
    )

    # q1 is new relative to the previous attempt but not unseen — attempt 1 had it.
    assert difference.new_question_count == 2
    assert difference.unseen_question_count == 1


@pytest.mark.parametrize("expected", [-5, 0])
def test_a_negative_expectation_is_clamped(expected):
    difference = compare_question_sets(
        previous_question_ids=("q1",),
        retake_question_ids=("q1",),
        expected_fresh_questions=expected,
    )
    assert difference.expected_fresh_questions == 0
    assert difference.satisfied is True
