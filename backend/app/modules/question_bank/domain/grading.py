"""Grading a learner response against a frozen snapshot.

Scope note: UC-02 owns the question bank, not quiz delivery. This grader exists because §16
requires a completed attempt to report the learner's response and score information, so the
delivery seam needs a way to compute them from the answer key it was given. It grades strictly
against the *snapshot*, never the live question — which is what makes a score reproducible
after the question has been edited or retired.

It also demonstrates the presentation-order / answer-order separation: grading a
DRAG_TO_ORDER response uses ``correct_position`` and completely ignores the order the options
happened to be shown in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.coercion import round4
from app.core.errors import FieldIssue
from app.modules.question_bank.domain.enums import QuestionType, ScoringStrategy
from app.modules.question_bank.domain.snapshots import SnapshotView


@dataclass(slots=True)
class GradeResult:
    is_correct: bool
    awarded_points: float
    max_points: float
    #: Per-label detail for review screens.
    breakdown: list[dict[str, object]] = field(default_factory=list)


def validate_response(
    snapshot: SnapshotView, selected_labels: list[str] | None, ordered_labels: list[str] | None
) -> list[FieldIssue]:
    """Check a learner response is expressible against this snapshot's option set."""
    issues: list[FieldIssue] = []
    known = {label.upper() for label in snapshot.labels}

    if snapshot.question_type is QuestionType.DRAG_TO_ORDER:
        if selected_labels:
            issues.append(
                FieldIssue(
                    field="selectedLabels",
                    code="RESPONSE_SHAPE_MISMATCH",
                    message="A drag-to-order question expects orderedLabels, not selectedLabels.",
                )
            )
        if ordered_labels is not None:
            unknown = [label for label in ordered_labels if label.upper() not in known]
            if unknown:
                issues.append(
                    FieldIssue(
                        field="orderedLabels",
                        code="UNKNOWN_OPTION_LABEL",
                        message="Response references unknown option(s): "
                        + ", ".join(unknown)
                        + ".",
                    )
                )
            if len({label.upper() for label in ordered_labels}) != len(ordered_labels):
                issues.append(
                    FieldIssue(
                        field="orderedLabels",
                        code="DUPLICATE_RESPONSE_LABEL",
                        message="An ordering response may not repeat the same item.",
                    )
                )
        return issues

    if ordered_labels:
        issues.append(
            FieldIssue(
                field="orderedLabels",
                code="RESPONSE_SHAPE_MISMATCH",
                message=(
                    f"A {snapshot.question_type.value} question expects selectedLabels, "
                    "not orderedLabels."
                ),
            )
        )
    if selected_labels is not None:
        unknown = [label for label in selected_labels if label.upper() not in known]
        if unknown:
            issues.append(
                FieldIssue(
                    field="selectedLabels",
                    code="UNKNOWN_OPTION_LABEL",
                    message="Response references unknown option(s): " + ", ".join(unknown) + ".",
                )
            )
        if len({label.upper() for label in selected_labels}) != len(selected_labels):
            issues.append(
                FieldIssue(
                    field="selectedLabels",
                    code="DUPLICATE_RESPONSE_LABEL",
                    message="A selection response may not repeat the same option.",
                )
            )
        if (
            snapshot.question_type
            in (QuestionType.SINGLE_CHOICE, QuestionType.TRUE_FALSE, QuestionType.SCENARIO)
            and len(selected_labels) > 1
        ):
            issues.append(
                FieldIssue(
                    field="selectedLabels",
                    code="TOO_MANY_SELECTIONS",
                    message=(
                        f"A {snapshot.question_type.value} question accepts at most one "
                        f"selection (received {len(selected_labels)})."
                    ),
                )
            )
    return issues


def grade(
    snapshot: SnapshotView,
    *,
    selected_labels: list[str] | None = None,
    ordered_labels: list[str] | None = None,
) -> GradeResult:
    """Grade a response against the snapshot's answer key."""
    max_points = round4(snapshot.points)

    if snapshot.question_type is QuestionType.DRAG_TO_ORDER:
        return _grade_order(snapshot, ordered_labels or [], max_points)
    return _grade_selection(snapshot, selected_labels or [], max_points)


def _grade_order(snapshot: SnapshotView, response: list[str], max_points: float) -> GradeResult:
    # The answer key comes from `correct_position`. The order the options were SHOWN in is
    # irrelevant here — that lives on QuestionUsage.presentation_order.
    expected = [label.upper() for label in snapshot.correct_order]
    actual = [label.upper() for label in response]

    breakdown: list[dict[str, object]] = []
    correct_positions = 0
    for index, expected_label in enumerate(expected):
        actual_label = actual[index] if index < len(actual) else None
        hit = actual_label == expected_label
        if hit:
            correct_positions += 1
        breakdown.append(
            {
                "position": index + 1,
                "expectedLabel": expected_label,
                "actualLabel": actual_label,
                "correct": hit,
            }
        )

    fully_correct = actual == expected

    if snapshot.scoring_strategy == ScoringStrategy.PARTIAL_CREDIT.value and expected:
        awarded = max_points * (correct_positions / len(expected))
    else:
        awarded = max_points if fully_correct else 0.0

    return GradeResult(
        is_correct=fully_correct,
        awarded_points=round4(max(0.0, awarded)),
        max_points=max_points,
        breakdown=breakdown,
    )


def _grade_selection(snapshot: SnapshotView, response: list[str], max_points: float) -> GradeResult:
    expected = {label.upper() for label in snapshot.correct_labels}
    actual = {label.upper() for label in response}

    hits = expected & actual
    misses = expected - actual
    false_positives = actual - expected
    fully_correct = bool(expected) and actual == expected

    breakdown = [
        {
            "label": option.label,
            "expectedCorrect": option.is_correct,
            "selected": option.label.upper() in actual,
        }
        for option in snapshot.options
    ]

    strategy = snapshot.scoring_strategy
    if strategy == ScoringStrategy.ALL_OR_NOTHING.value or not expected:
        awarded = max_points if fully_correct else 0.0
    else:
        awarded = max_points * (len(hits) / len(expected))
        if strategy == ScoringStrategy.PARTIAL_CREDIT_WITH_PENALTY.value:
            awarded -= snapshot.penalty_per_incorrect * len(false_positives)

    return GradeResult(
        is_correct=fully_correct,
        # Partial credit never goes negative — a penalty caps out at zero for the question.
        awarded_points=round4(min(max_points, max(0.0, awarded))),
        max_points=max_points,
        breakdown=breakdown
        + [
            {
                "summary": {
                    "expected": sorted(expected),
                    "selected": sorted(actual),
                    "missed": sorted(misses),
                    "incorrectlySelected": sorted(false_positives),
                }
            }
        ],
    )
