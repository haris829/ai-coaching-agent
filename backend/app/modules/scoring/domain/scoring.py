"""The scoring rules. Pure functions over frozen data.

One function per question type, plus the aggregation that turns per-question marks into a total, a
maximum and a percentage. No persistence, no HTTP, no clock: everything here is decided by its
arguments alone, which is why the rules can be tested exhaustively without a database.

The five rules, as UC-04 defines them
-------------------------------------
============== ====================================================================
Type           Rule
============== ====================================================================
SINGLE_CHOICE  correct -> full marks; anything else -> 0
TRUE_FALSE     correct -> full marks; anything else -> 0
MULTI_SELECT   the configured marking policy: pro-rata marks, minus the configured
               deduction per incorrect selection, floored at 0 for the question
SCENARIO       only the configured **primary** answer is scored; correct -> full marks
DRAG_TO_ORDER  the exact sequence is required; there is no partial credit
============== ====================================================================

Two rules apply to every type: an **unanswered** question scores 0, and a question's score can
never be negative -- a deduction stops at zero for that question and never eats into another's
marks.

Where this differs from ``question_bank.domain.grading``
-------------------------------------------------------
UC-02 has a grader of its own. It exists for a different job: scoring one delivered response so
the question bank can render its historical usage report, and it honours the authored strategy for
every type -- including partial credit for a drag-to-order response. UC-04 owns *quiz* scoring,
and the quiz's rule for drag-to-order is exact-sequence-only. Where the two differ, this module is
authoritative for an attempt's result; UC-02's stays authoritative for the bank's own report. They
are not two implementations of one rule, and neither reads the other."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.coercion import round4
from app.modules.scoring.domain.answer_key import AnswerKey, MarkingPolicy
from app.modules.scoring.domain.enums import (
    AnswerKeySource,
    QuestionOutcome,
    QuestionType,
    ScoreAnomaly,
)
from app.modules.scoring.integration.attempt_delivery.types import DeliveredQuestion

#: Text used when an option carries none, so a report never renders an empty cell.
UNKNOWN_OPTION_TEXT = "(option text unavailable)"


@dataclass(frozen=True, slots=True)
class OptionMark:
    """One option's contribution to a question's marks.

    Required for MULTI_SELECT feedback (UC-06): the learner must be able to see, per option, whether
    it was correct and what it contributed. Produced for every choice-based type because the shape
    costs nothing and makes the feedback report uniform.
    """

    option_id: str
    text: str
    selected: bool
    correct: bool
    mark_contribution: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "optionId": self.option_id,
            "text": self.text,
            "selected": self.selected,
            "correct": self.correct,
            "markContribution": self.mark_contribution,
        }


@dataclass(frozen=True, slots=True)
class QuestionScore:
    """The marked result for one delivered question."""

    question_id: str
    question_version: int
    question_type: QuestionType
    position: int
    attempt_question_id: str

    awarded_marks: float
    maximum_marks: float
    #: Marks before the per-question floor at zero. Equals ``awarded_marks`` unless a deduction bit.
    raw_marks: float
    #: Total deducted for incorrect selections, as a positive number.
    deduction: float
    outcome: QuestionOutcome
    answered: bool

    #: The learner's answer, rendered for a human -- option texts, not ids alone.
    learner_answer_display: dict[str, Any]
    #: The correct answer, rendered the same way.
    correct_answer_display: dict[str, Any]
    option_marks: tuple[OptionMark, ...] = ()
    #: Set when this question could not be scored; the result then stays PENDING_SCORE.
    anomaly: ScoreAnomaly | None = None
    key_source: AnswerKeySource | None = None
    #: UC-02's authored explanation for this version, carried through for UC-06.
    explanation: str | None = None
    topics: tuple[str, ...] = ()

    @property
    def scored(self) -> bool:
        return self.anomaly is None


@dataclass(frozen=True, slots=True)
class ResultTotals:
    """The attempt-level arithmetic."""

    total_marks: float
    maximum_marks: float
    percentage: float
    total_questions: int
    correct_count: int
    incorrect_count: int
    unanswered_count: int
    partially_correct_count: int
    not_scored_count: int
    anomalies: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def confirmable(self) -> bool:
        """Whether these totals may be confirmed as a score.

        Any anomaly blocks confirmation. A percentage computed from a broken answer key or a zero
        maximum is worse than no percentage at all: pass/fail and a certificate are gated on it.
        """
        return not self.anomalies


# ---------------------------------------------------------------------------
# Reading a learner response
# ---------------------------------------------------------------------------


def _selected_option_id(response: dict[str, Any] | None) -> str | None:
    value = (response or {}).get("selectedOptionId")
    return value if isinstance(value, str) and value else None


def _selected_option_ids(response: dict[str, Any] | None) -> list[str] | None:
    value = (response or {}).get("selectedOptionIds")
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str) and item]


def _ordered_item_ids(response: dict[str, Any] | None) -> list[str] | None:
    value = (response or {}).get("orderedItemIds")
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str) and item]


def _boolean_value(response: dict[str, Any] | None) -> bool | None:
    value = (response or {}).get("value")
    return value if isinstance(value, bool) else None


def _scenario_responses(response: dict[str, Any] | None) -> list[dict[str, Any]]:
    value = (response or {}).get("responses")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _truth_option_id(key: AnswerKey, truth: bool) -> str:
    """The option id a True/False answer of ``truth`` corresponds to.

    UC-02 fixes the pair as the labels ``TRUE`` and ``FALSE``, so the mapping is by identity rather
    than by position -- a shuffled presentation cannot invert the marking.
    """
    wanted = "TRUE" if truth else "FALSE"
    for option in key.options:
        if option.option_id.upper() == wanted:
            return option.option_id
    return wanted


# ---------------------------------------------------------------------------
# Display rendering
# ---------------------------------------------------------------------------


def _display_options(key: AnswerKey, option_ids: list[str]) -> dict[str, Any]:
    return {
        "optionIds": list(option_ids),
        "labels": [key.text_for(option_id) or UNKNOWN_OPTION_TEXT for option_id in option_ids],
    }


def _empty_display() -> dict[str, Any]:
    return {"optionIds": [], "labels": []}


# ---------------------------------------------------------------------------
# Per-type scoring
# ---------------------------------------------------------------------------


def _option_marks(
    key: AnswerKey,
    delivered: DeliveredQuestion,
    selected: set[str],
    contributions: dict[str, float],
) -> tuple[OptionMark, ...]:
    """Build the per-option breakdown in the order the learner saw the options."""
    presented = [option.option_id for option in delivered.options] or list(key.option_ids)
    texts = {option.option_id: option.text for option in delivered.options}
    return tuple(
        OptionMark(
            option_id=option_id,
            text=texts.get(option_id) or key.text_for(option_id) or UNKNOWN_OPTION_TEXT,
            selected=option_id in selected,
            correct=option_id in key.correct_option_ids,
            mark_contribution=round4(contributions.get(option_id, 0.0)),
        )
        for option_id in presented
    )


def _all_or_nothing(
    key: AnswerKey,
    delivered: DeliveredQuestion,
    *,
    chosen: list[str],
    correct_ids: list[str],
    is_correct: bool,
) -> QuestionScore:
    """The shared shape for the four types that have no partial credit."""
    awarded = key.max_marks if is_correct else 0.0
    contributions = dict.fromkeys(chosen, awarded if is_correct else 0.0)
    return QuestionScore(
        question_id=key.question_id,
        question_version=key.question_version,
        question_type=key.question_type,
        position=delivered.position,
        attempt_question_id=delivered.attempt_question_id,
        awarded_marks=round4(awarded),
        maximum_marks=key.max_marks,
        raw_marks=round4(awarded),
        deduction=0.0,
        outcome=QuestionOutcome.CORRECT if is_correct else QuestionOutcome.INCORRECT,
        answered=True,
        learner_answer_display=_display_options(key, chosen),
        correct_answer_display=_display_options(key, correct_ids),
        option_marks=_option_marks(key, delivered, set(chosen), contributions),
        key_source=key.source,
        explanation=key.explanation,
        topics=key.topics,
    )


def _score_single_choice(key: AnswerKey, delivered: DeliveredQuestion) -> QuestionScore:
    selected = _selected_option_id(delivered.response)
    if selected is None:
        return _unreadable(key, delivered)
    return _all_or_nothing(
        key,
        delivered,
        chosen=[selected],
        correct_ids=list(key.correct_option_ids),
        is_correct=selected in key.correct_option_ids,
    )


def _score_true_false(key: AnswerKey, delivered: DeliveredQuestion) -> QuestionScore:
    value = _boolean_value(delivered.response)
    if value is None:
        # A True/False answer may also arrive as a plain selection when a client chose the option
        # rather than the boolean; both express the same thing, so both are accepted.
        selected = _selected_option_id(delivered.response)
        if selected is None:
            return _unreadable(key, delivered)
        chosen = selected
    else:
        chosen = _truth_option_id(key, value)
    return _all_or_nothing(
        key,
        delivered,
        chosen=[chosen],
        correct_ids=list(key.correct_option_ids),
        is_correct=chosen in key.correct_option_ids,
    )


def _score_scenario(key: AnswerKey, delivered: DeliveredQuestion) -> QuestionScore:
    """Score only the configured primary answer.

    UC-02 models a scenario as a vignette plus one question with exactly one primary answer, and
    UC-03 delivers that as a single sub-question. Anything else the learner filled in is not part of
    the marking, which is what "score only the configured primary answer" means.
    """
    primary_option_id = key.primary_option_id
    if primary_option_id is None:
        return _anomalous(key, delivered, ScoreAnomaly.AMBIGUOUS_PRIMARY_ANSWER)

    responses = _scenario_responses(delivered.response)
    if not responses:
        return _unreadable(key, delivered)

    # The primary sub-question is the first one UC-03 delivered; a scenario has exactly one.
    primary_sub_id = (
        delivered.sub_question_ids[0]
        if delivered.sub_question_ids
        else str(responses[0].get("subQuestionId") or "")
    )
    answer: dict[str, Any] | None = None
    for entry in responses:
        if str(entry.get("subQuestionId") or "") == primary_sub_id:
            candidate = entry.get("answer")
            answer = candidate if isinstance(candidate, dict) else None
            break

    if answer is None:
        # The learner answered a non-primary sub-question only: the primary is unanswered, and the
        # rule for an unanswered question is zero -- not an error.
        return _unanswered(key, delivered)

    selected = _selected_option_id(answer)
    if selected is None:
        return _unreadable(key, delivered)

    return _all_or_nothing(
        key,
        delivered,
        chosen=[selected],
        correct_ids=[primary_option_id],
        is_correct=selected == primary_option_id,
    )


def _score_drag_to_order(key: AnswerKey, delivered: DeliveredQuestion) -> QuestionScore:
    """Exact sequence, no partial credit -- however the items happened to be presented."""
    ordered = _ordered_item_ids(delivered.response)
    if ordered is None:
        return _unreadable(key, delivered)

    expected = list(key.correct_order)
    is_correct = ordered == expected
    awarded = key.max_marks if is_correct else 0.0

    return QuestionScore(
        question_id=key.question_id,
        question_version=key.question_version,
        question_type=key.question_type,
        position=delivered.position,
        attempt_question_id=delivered.attempt_question_id,
        awarded_marks=round4(awarded),
        maximum_marks=key.max_marks,
        raw_marks=round4(awarded),
        deduction=0.0,
        outcome=QuestionOutcome.CORRECT if is_correct else QuestionOutcome.INCORRECT,
        answered=True,
        learner_answer_display={
            "orderedItemIds": ordered,
            "labels": [key.text_for(item_id) or UNKNOWN_OPTION_TEXT for item_id in ordered],
        },
        correct_answer_display={
            "orderedItemIds": expected,
            "labels": [key.text_for(item_id) or UNKNOWN_OPTION_TEXT for item_id in expected],
        },
        option_marks=tuple(
            OptionMark(
                option_id=item_id,
                text=key.text_for(item_id) or UNKNOWN_OPTION_TEXT,
                selected=True,
                # "Correct" per position: the item the learner placed here belongs here.
                correct=index < len(expected) and expected[index] == item_id,
                mark_contribution=0.0,
            )
            for index, item_id in enumerate(ordered)
        ),
        key_source=key.source,
        explanation=key.explanation,
        topics=key.topics,
    )


def _score_multi_select(key: AnswerKey, delivered: DeliveredQuestion) -> QuestionScore:
    """Configured positive marks and deductions, floored at zero for the question."""
    selected_list = _selected_option_ids(delivered.response)
    if selected_list is None:
        return _unreadable(key, delivered)

    selected = set(selected_list)
    correct = set(key.correct_option_ids)
    hits = selected & correct
    false_positives = selected - correct

    per_correct = key.max_marks / len(correct) if correct else 0.0
    fully_correct = selected == correct

    contributions: dict[str, float] = {}
    if key.marking_policy is MarkingPolicy.EXACT:
        # Nothing is earned unless the whole set matches, so each selected correct option's
        # contribution is its share of the marks that were actually earned.
        earned = key.max_marks if fully_correct else 0.0
        deduction = 0.0
        raw = earned
        share = earned / len(hits) if hits else 0.0
        contributions = dict.fromkeys(hits, share)
    else:
        gained = per_correct * len(hits)
        deduction = (
            key.deduction_per_incorrect * len(false_positives)
            if key.marking_policy is MarkingPolicy.PARTIAL_WITH_DEDUCTION
            else 0.0
        )
        raw = gained - deduction
        contributions = dict.fromkeys(hits, per_correct)
        if deduction:
            contributions.update(dict.fromkeys(false_positives, -key.deduction_per_incorrect))

    # The floor is per question: a deduction can take this question to zero and no further, and can
    # never reach into another question's marks.
    awarded = min(key.max_marks, max(0.0, raw))

    if fully_correct:
        outcome = QuestionOutcome.CORRECT
    elif awarded > 0:
        outcome = QuestionOutcome.PARTIALLY_CORRECT
    else:
        outcome = QuestionOutcome.INCORRECT

    return QuestionScore(
        question_id=key.question_id,
        question_version=key.question_version,
        question_type=key.question_type,
        position=delivered.position,
        attempt_question_id=delivered.attempt_question_id,
        awarded_marks=round4(awarded),
        maximum_marks=key.max_marks,
        raw_marks=round4(raw),
        deduction=round4(deduction),
        outcome=outcome,
        answered=True,
        learner_answer_display=_display_options(key, sorted(selected)),
        correct_answer_display=_display_options(key, sorted(correct)),
        option_marks=_option_marks(key, delivered, selected, contributions),
        key_source=key.source,
        explanation=key.explanation,
        topics=key.topics,
    )


# ---------------------------------------------------------------------------
# Zero-mark outcomes
# ---------------------------------------------------------------------------


def _zero(
    key: AnswerKey,
    delivered: DeliveredQuestion,
    *,
    outcome: QuestionOutcome,
    answered: bool,
    anomaly: ScoreAnomaly | None = None,
) -> QuestionScore:
    correct_ids = (
        list(key.correct_order)
        if key.question_type is QuestionType.DRAG_TO_ORDER
        else list(key.correct_option_ids)
    )
    correct_display = (
        {
            "orderedItemIds": correct_ids,
            "labels": [key.text_for(item) or UNKNOWN_OPTION_TEXT for item in correct_ids],
        }
        if key.question_type is QuestionType.DRAG_TO_ORDER
        else _display_options(key, correct_ids)
    )
    return QuestionScore(
        question_id=key.question_id,
        question_version=key.question_version,
        question_type=key.question_type,
        position=delivered.position,
        attempt_question_id=delivered.attempt_question_id,
        awarded_marks=0.0,
        maximum_marks=key.max_marks,
        raw_marks=0.0,
        deduction=0.0,
        outcome=outcome,
        answered=answered,
        learner_answer_display=_empty_display(),
        correct_answer_display=correct_display,
        option_marks=_option_marks(key, delivered, set(), {}),
        anomaly=anomaly,
        key_source=key.source,
        explanation=key.explanation,
        topics=key.topics,
    )


def _unanswered(key: AnswerKey, delivered: DeliveredQuestion) -> QuestionScore:
    """An unanswered question scores zero. Not an error, and not an anomaly."""
    return _zero(key, delivered, outcome=QuestionOutcome.UNANSWERED, answered=False)


def _unreadable(key: AnswerKey, delivered: DeliveredQuestion) -> QuestionScore:
    """A stored answer that does not fit the delivered question's shape.

    UC-03 validates every answer against the delivered snapshot before storing it, so this is
    unreachable through the API. It is still handled rather than allowed to raise: one corrupt row
    must leave that attempt reported as pending, never crash the scoring of a whole cohort.
    """
    return _zero(
        key,
        delivered,
        outcome=QuestionOutcome.NOT_SCORED,
        answered=True,
        anomaly=ScoreAnomaly.UNREADABLE_ANSWER,
    )


def _anomalous(
    key: AnswerKey, delivered: DeliveredQuestion, anomaly: ScoreAnomaly
) -> QuestionScore:
    return _zero(
        key,
        delivered,
        outcome=QuestionOutcome.NOT_SCORED,
        answered=delivered.answered,
        anomaly=anomaly,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

_SCORERS = {
    QuestionType.SINGLE_CHOICE: _score_single_choice,
    QuestionType.TRUE_FALSE: _score_true_false,
    QuestionType.MULTI_SELECT: _score_multi_select,
    QuestionType.SCENARIO: _score_scenario,
    QuestionType.DRAG_TO_ORDER: _score_drag_to_order,
}


def score_question(delivered: DeliveredQuestion, key: AnswerKey | None) -> QuestionScore:
    """Mark one delivered question against its answer key.

    ``key`` is ``None`` when no usable answer key could be resolved. That is reported as
    ``MISSING_ANSWER_KEY`` rather than scored as zero, because a zero the learner did not earn is
    indistinguishable from a wrong answer once it is stored.
    """
    if key is None or not key.is_usable():
        return _missing_key(delivered, key)

    # The marks the attempt froze are authoritative for this attempt.
    resolved = key.with_max_marks(delivered.max_marks)

    if resolved.question_type is not delivered.question_type:
        return _anomalous(resolved, delivered, ScoreAnomaly.UNSUPPORTED_QUESTION_TYPE)
    if not delivered.answered:
        return _unanswered(resolved, delivered)

    scorer = _SCORERS.get(resolved.question_type)
    if scorer is None:
        # pragma: no cover - the map covers the whole enum
        return _anomalous(resolved, delivered, ScoreAnomaly.UNSUPPORTED_QUESTION_TYPE)
    return scorer(resolved, delivered)


def _missing_key(delivered: DeliveredQuestion, key: AnswerKey | None) -> QuestionScore:
    return QuestionScore(
        question_id=delivered.question_id,
        question_version=delivered.question_version,
        question_type=delivered.question_type,
        position=delivered.position,
        attempt_question_id=delivered.attempt_question_id,
        awarded_marks=0.0,
        maximum_marks=round4(max(0.0, delivered.max_marks)),
        raw_marks=0.0,
        deduction=0.0,
        outcome=QuestionOutcome.NOT_SCORED,
        answered=delivered.answered,
        learner_answer_display=_empty_display(),
        correct_answer_display=_empty_display(),
        option_marks=(),
        anomaly=ScoreAnomaly.MISSING_ANSWER_KEY,
        key_source=key.source if key is not None else None,
        explanation=key.explanation if key is not None else None,
        topics=key.topics if key is not None else (),
    )


def aggregate(scores: list[QuestionScore]) -> ResultTotals:
    """Total the marks, the maximum and the percentage.

    The percentage is computed from the *sum of the delivered questions' marks*, not from the
    configured question count, so a question worth two marks counts twice as much as one worth one.
    """
    total = round4(sum(score.awarded_marks for score in scores))
    maximum = round4(sum(score.maximum_marks for score in scores))

    anomalies: list[dict[str, Any]] = [
        {
            "code": str(score.anomaly),
            "questionId": score.question_id,
            "position": score.position,
        }
        for score in scores
        if score.anomaly is not None
    ]

    if not scores:
        anomalies.append({"code": str(ScoreAnomaly.NO_QUESTIONS_DELIVERED)})
    elif maximum <= 0:
        # A zero maximum makes a percentage undefined, and pass/fail would be gated on it.
        anomalies.append({"code": str(ScoreAnomaly.ZERO_MAXIMUM_MARKS)})

    percentage = round(total / maximum * 100, 2) if maximum > 0 else 0.0

    return ResultTotals(
        total_marks=total,
        maximum_marks=maximum,
        percentage=percentage,
        total_questions=len(scores),
        correct_count=sum(1 for s in scores if s.outcome is QuestionOutcome.CORRECT),
        incorrect_count=sum(
            1
            for s in scores
            if s.outcome in (QuestionOutcome.INCORRECT, QuestionOutcome.PARTIALLY_CORRECT)
        ),
        unanswered_count=sum(1 for s in scores if s.outcome is QuestionOutcome.UNANSWERED),
        partially_correct_count=sum(
            1 for s in scores if s.outcome is QuestionOutcome.PARTIALLY_CORRECT
        ),
        not_scored_count=sum(1 for s in scores if s.outcome is QuestionOutcome.NOT_SCORED),
        anomalies=tuple(anomalies),
    )
