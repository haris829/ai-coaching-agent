"""Exclusion-aware question planning (§5, §6, §7, §8).

UC-08 does not select questions. It decides **what a retake should avoid**, and hands that to
UC-03's existing selector as a preference::

    questions the learner has already seen
            ↓
    which of them can be excluded and still fill the paper?
            ↓
    exclusion set  +  how many questions can genuinely be new
            ↓
    UC-03 selection (count, quotas, randomisation, eligibility — unchanged)

THE TIERS
---------
Excluding the learner's whole history is the best outcome and is tried first. When the bank
cannot support it the plan degrades one step at a time rather than giving up:

=========================  ==============================================================
``ALL_PREVIOUS_ATTEMPTS``   Nothing the learner has ever seen at this quiz is offered again.
``PREVIOUS_ATTEMPT_ONLY``   The immediately preceding paper is avoided; an older question may
                            return. Still satisfies §7 — every delivered question differs from
                            the attempt being retaken.
``NONE``                    The bank is too small to avoid reuse. The paper is still filled, and
                            the reuse is recorded rather than hidden (§8).
=========================  ==============================================================

A tier is chosen only if the remaining pool can *fully* satisfy the configuration — the total
count and every per-type quota. Half-excluding is never an option, because a paper short of its
configured count is not a valid paper.

WHAT IS NEVER DONE TO AVOID REUSE
---------------------------------
* A retired or otherwise ineligible question is never reached for. The pool this module counts
  has already excluded them at the UC-02 boundary, so they cannot enter the arithmetic.
* A type quota is never bent. Feasibility is computed per type, so "enough questions overall"
  never papers over "not enough SCENARIO questions".

Pure functions. Nothing here does I/O, and every decision it makes is reproducible from its
arguments — which is what lets §8's small-bank behaviour be asserted directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.modules.retakes.domain.enums import ExclusionScope, QuestionReuseReason
from app.modules.retakes.integration.uc01 import QuizConfigurationVersion
from app.modules.retakes.integration.uc02 import QuestionDescriptor


@dataclass(frozen=True, slots=True)
class QuestionTypeAvailability:
    """Per-type arithmetic, so a shortfall names the type that caused it."""

    type: str
    required: int
    eligible: int
    unused: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "required": self.required,
            "eligible": self.eligible,
            "unused": self.unused,
        }


@dataclass(frozen=True, slots=True)
class RetakeQuestionPlan:
    """The instruction UC-08 gives UC-03, plus the evidence behind it.

    ``expected_fresh_questions`` is the number the difference check in ``domain.difference``
    measures the delivered paper against: the most questions that *can* differ from the previous
    attempt given this bank and this configuration. When it equals the configured count, an
    entirely new paper was possible and anything less is a defect; when it is lower, the shortfall
    was unavoidable and is reported as such rather than as a failure.
    """

    required_count: int
    eligible_pool_size: int
    #: Questions in the eligible pool the learner has never been delivered at this quiz.
    unused_pool_size: int
    #: Ordered for determinism, so two identical plans produce identical requests.
    excluded_question_ids: tuple[str, ...]
    exclusion_scope: ExclusionScope
    #: True when the paper must contain at least one question the learner has already seen.
    reuse_expected: bool
    reuse_reason: QuestionReuseReason | None
    #: The most questions that can differ from the attempt being retaken.
    expected_fresh_questions: int
    type_availability: tuple[QuestionTypeAvailability, ...] = field(default_factory=tuple)
    #: False when the bank cannot fill the paper at all, even reusing everything.
    feasible: bool = True
    shortfalls: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_count": self.required_count,
            "eligible_pool_size": self.eligible_pool_size,
            "unused_pool_size": self.unused_pool_size,
            "excluded_question_count": len(self.excluded_question_ids),
            "exclusion_scope": self.exclusion_scope.value,
            "reuse_expected": self.reuse_expected,
            "reuse_reason": self.reuse_reason.value if self.reuse_reason else None,
            "expected_fresh_questions": self.expected_fresh_questions,
            "type_availability": [item.as_dict() for item in self.type_availability],
            "feasible": self.feasible,
            "shortfalls": list(self.shortfalls),
        }


# ---------------------------------------------------------------------------
# Pool arithmetic
# ---------------------------------------------------------------------------


def eligible_pool(
    pool: Sequence[QuestionDescriptor], config: QuizConfigurationVersion
) -> tuple[QuestionDescriptor, ...]:
    """Narrow a bank pool to what this configuration version could actually deliver.

    Retired questions are dropped again here even though the UC-02 query already excluded them:
    a permissive adapter must not be able to leak one into a retake, and the cost of the second
    filter is a boolean test (the same belt-and-braces UC-03's selector applies).
    """
    permitted = _permitted_types(config)
    return tuple(
        question
        for question in pool
        if not question.retired and (not permitted or question.question_type in permitted)
    )


def _permitted_types(config: QuizConfigurationVersion) -> frozenset[str]:
    """The types a paper may contain: the quota types, or the allowed list, or anything."""
    quotas = [quota for quota in config.question_type_quotas if quota.count > 0]
    if quotas:
        return frozenset(quota.type for quota in quotas)
    return frozenset(config.allowed_question_types)


def _count_by_type(pool: Iterable[QuestionDescriptor]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for question in pool:
        counts[question.question_type] = counts.get(question.question_type, 0) + 1
    return counts


def _capacity(
    config: QuizConfigurationVersion, available: Mapping[str, int], total_available: int
) -> int:
    """How many of the configured questions this availability can supply.

    With quotas, each type is capped independently — surplus SINGLE_CHOICE questions cannot make
    up a SCENARIO shortfall, which is precisely the invariant §8 says must not be violated.
    Without quotas the pool is fungible across the permitted types.
    """
    quotas = [quota for quota in config.question_type_quotas if quota.count > 0]
    if quotas:
        return sum(min(quota.count, available.get(quota.type, 0)) for quota in quotas)
    return min(config.question_count, total_available)


def _availability_rows(
    config: QuizConfigurationVersion,
    eligible_counts: Mapping[str, int],
    unused_counts: Mapping[str, int],
    eligible_total: int,
    unused_total: int,
) -> tuple[QuestionTypeAvailability, ...]:
    quotas = [quota for quota in config.question_type_quotas if quota.count > 0]
    if quotas:
        return tuple(
            QuestionTypeAvailability(
                type=quota.type,
                required=quota.count,
                eligible=eligible_counts.get(quota.type, 0),
                unused=unused_counts.get(quota.type, 0),
            )
            for quota in quotas
        )
    # No quotas: the types are interchangeable, so one row describes the whole requirement.
    return (
        QuestionTypeAvailability(
            type="ANY",
            required=config.question_count,
            eligible=eligible_total,
            unused=unused_total,
        ),
    )


def _shortfall_rows(
    config: QuizConfigurationVersion, available: Mapping[str, int], total_available: int
) -> tuple[dict[str, Any], ...]:
    quotas = [quota for quota in config.question_type_quotas if quota.count > 0]
    if quotas:
        return tuple(
            {
                "type": quota.type,
                "required": quota.count,
                "available": available.get(quota.type, 0),
            }
            for quota in quotas
            if available.get(quota.type, 0) < quota.count
        )
    if total_available < config.question_count:
        return (
            {
                "type": "ANY",
                "required": config.question_count,
                "available": total_available,
            },
        )
    return ()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_retake_questions(
    *,
    config: QuizConfigurationVersion,
    pool: Sequence[QuestionDescriptor],
    previous_attempt_question_ids: Sequence[str],
    historical_question_ids: Iterable[str] = (),
) -> RetakeQuestionPlan:
    """Decide what a retake should exclude, and what that means for the paper.

    ``previous_attempt_question_ids`` is the paper being retaken. ``historical_question_ids`` is
    everything the learner has ever been delivered at this quiz — normally a superset, and the
    preferred exclusion set.
    """
    candidates = eligible_pool(pool, config)
    eligible_counts = _count_by_type(candidates)
    eligible_total = len(candidates)

    previous_ids = frozenset(previous_attempt_question_ids)
    history_ids = frozenset(historical_question_ids) | previous_ids

    unused = tuple(q for q in candidates if q.question_id not in history_ids)
    unused_counts = _count_by_type(unused)
    unused_total = len(unused)

    availability = _availability_rows(
        config, eligible_counts, unused_counts, eligible_total, unused_total
    )

    # Can the bank fill the paper at all, reusing whatever it must? If not, no exclusion tier
    # will help and the caller refuses rather than delivering a short paper.
    if _capacity(config, eligible_counts, eligible_total) < config.question_count:
        return RetakeQuestionPlan(
            required_count=config.question_count,
            eligible_pool_size=eligible_total,
            unused_pool_size=unused_total,
            excluded_question_ids=(),
            exclusion_scope=ExclusionScope.NONE,
            reuse_expected=True,
            reuse_reason=QuestionReuseReason.INSUFFICIENT_UNUSED_QUESTIONS,
            expected_fresh_questions=0,
            type_availability=availability,
            feasible=False,
            shortfalls=_shortfall_rows(config, eligible_counts, eligible_total),
        )

    # The most questions that can differ from the paper being retaken. Independent of which tier
    # is chosen, because it is a property of the bank, not of the exclusion decision.
    without_previous = tuple(q for q in candidates if q.question_id not in previous_ids)
    expected_fresh = _capacity(
        config, _count_by_type(without_previous), len(without_previous)
    )

    # Tier 1 — exclude everything the learner has ever seen.
    if _capacity(config, unused_counts, unused_total) >= config.question_count:
        return RetakeQuestionPlan(
            required_count=config.question_count,
            eligible_pool_size=eligible_total,
            unused_pool_size=unused_total,
            excluded_question_ids=tuple(sorted(history_ids)),
            exclusion_scope=ExclusionScope.ALL_PREVIOUS_ATTEMPTS,
            reuse_expected=False,
            reuse_reason=None,
            expected_fresh_questions=expected_fresh,
            type_availability=availability,
        )

    # Tier 2 — exclude only the paper being retaken.
    previous_counts = _count_by_type(without_previous)
    if _capacity(config, previous_counts, len(without_previous)) >= config.question_count:
        return RetakeQuestionPlan(
            required_count=config.question_count,
            eligible_pool_size=eligible_total,
            unused_pool_size=unused_total,
            excluded_question_ids=tuple(sorted(previous_ids)),
            exclusion_scope=ExclusionScope.PREVIOUS_ATTEMPT_ONLY,
            # A question from an *older* attempt may return, so this is still reuse — just never
            # reuse of the paper the learner has most recently seen.
            reuse_expected=True,
            reuse_reason=_reuse_reason(config, unused_counts, unused_total),
            expected_fresh_questions=expected_fresh,
            type_availability=availability,
        )

    # Tier 3 — the bank cannot avoid repeating part of the previous paper.
    return RetakeQuestionPlan(
        required_count=config.question_count,
        eligible_pool_size=eligible_total,
        unused_pool_size=unused_total,
        # Still passed to UC-03: even when the previous questions cannot all be avoided, they are
        # the ones to reach for last.
        excluded_question_ids=tuple(sorted(previous_ids)),
        exclusion_scope=ExclusionScope.NONE,
        reuse_expected=True,
        reuse_reason=_reuse_reason(config, previous_counts, len(without_previous)),
        expected_fresh_questions=expected_fresh,
        type_availability=availability,
    )


def _reuse_reason(
    config: QuizConfigurationVersion, available: Mapping[str, int], total_available: int
) -> QuestionReuseReason:
    """Distinguish "not enough questions" from "not enough of the right type".

    Worth separating: the first is answered by writing more questions, the second by writing more
    questions *of one type*, and an administrator reading the retake record should not have to
    work out which.
    """
    shortfalls = _shortfall_rows(config, available, total_available)
    if any(row["type"] != "ANY" for row in shortfalls):
        return QuestionReuseReason.INSUFFICIENT_UNUSED_QUESTIONS_OF_TYPE
    return QuestionReuseReason.INSUFFICIENT_UNUSED_QUESTIONS
