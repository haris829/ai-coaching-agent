"""Map a parsed CSV row onto a ``QuestionDraft`` (UC-02 §19).

This layer owns only *CSV-shaped* problems — the ones that cannot be expressed once the row has
become a draft:

* an option cell that is not ``LABEL:Text``;
* ``correct_answers`` naming a label that is not in ``options``;
* ``correct_order`` that does not cover exactly the option labels;
* a column populated for the wrong question type (e.g. ``correct_order`` on a single-choice row).

Everything else — required text, option counts, exactly-one-correct, scoring validity — is
delegated to the authoritative domain validator, so CSV and the JSON API cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import FieldIssue
from app.modules.question_bank.csv_import.parser import ParsedRow, split_label_text, split_list
from app.modules.question_bank.domain.drafts import OptionDraft, QuestionDraft, ScoringDraft
from app.modules.question_bank.domain.enums import (
    TRUE_FALSE_LABELS,
    QuestionType,
    enum_values,
    parse_enum,
)


@dataclass(slots=True)
class MappedRow:
    draft: QuestionDraft | None
    issues: list[FieldIssue] = field(default_factory=list)


def map_row(row: ParsedRow) -> MappedRow:
    """Convert one CSV row into a draft, collecting CSV-level issues."""
    issues: list[FieldIssue] = list(row.issues)

    raw_type = row.get("type").strip()
    question_type = parse_enum(QuestionType, raw_type)
    if question_type is None:
        # Without a known type nothing else can be interpreted reliably, so stop here and
        # report the single actionable problem.
        issues.append(
            FieldIssue(
                field="type",
                code="INVALID_QUESTION_TYPE" if raw_type else "QUESTION_TYPE_REQUIRED",
                message=(
                    f'Invalid question type: "{raw_type}". Expected one of '
                    f"{', '.join(enum_values(QuestionType))}."
                    if raw_type
                    else "A question type is required."
                ),
            )
        )
        return MappedRow(draft=None, issues=issues)

    options, option_issues = _build_options(row, question_type)
    issues.extend(option_issues)

    labels = {option.label.upper(): option for option in options}

    correct_raw = split_list(row.get("correct_answers"))
    order_raw = split_list(row.get("correct_order"))

    if question_type is QuestionType.DRAG_TO_ORDER:
        issues.extend(_apply_drag_order(options, labels, correct_raw, order_raw))
    else:
        issues.extend(
            _apply_correct_answers(question_type, options, labels, correct_raw, order_raw)
        )

    scenario_text = row.get("scenario_text").strip()
    if scenario_text and question_type is not QuestionType.SCENARIO:
        issues.append(
            FieldIssue(
                field="scenario_text",
                code="SCENARIO_TEXT_NOT_ALLOWED",
                message=(
                    "scenario_text must be empty for "
                    f"{question_type.value} questions; it is only used by SCENARIO."
                ),
            )
        )

    draft = QuestionDraft(
        type=question_type.value,
        status=None,  # CSV imports land as ACTIVE (the validator's default)
        question_text=row.get("question_text"),
        scenario_text=scenario_text or None,
        explanation=row.get("explanation"),
        difficulty=row.get("difficulty") or None,
        external_ref=row.get("external_ref") or None,
        options=options,
        topics=split_list(row.get("topics")),
        topic_ids=[],
        scoring=ScoringDraft(
            points=row.get("points") or None,
            scoring_strategy=row.get("scoring_strategy") or None,
            penalty_per_incorrect=row.get("penalty_per_incorrect") or None,
        ),
    )

    return MappedRow(draft=draft, issues=issues)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def _build_options(
    row: ParsedRow, question_type: QuestionType
) -> tuple[list[OptionDraft], list[FieldIssue]]:
    issues: list[FieldIssue] = []
    raw_cells = split_list(row.get("options"))

    if not raw_cells and question_type is QuestionType.TRUE_FALSE:
        # The TRUE/FALSE pair is implied; correctness is applied from correct_answers.
        return (
            [
                OptionDraft(label="TRUE", text="True", position=1),
                OptionDraft(label="FALSE", text="False", position=2),
            ],
            issues,
        )

    options: list[OptionDraft] = []
    for index, cell in enumerate(raw_cells):
        parts = split_label_text(cell)
        if parts is None:
            issues.append(
                FieldIssue(
                    field="options",
                    code="OPTION_FORMAT_INVALID",
                    message=(
                        f'Option {index + 1} ("{cell}") is not in LABEL:Text format. '
                        "Use for example A:Paris|B:London."
                    ),
                )
            )
            continue
        label, text = parts
        if not label:
            issues.append(
                FieldIssue(
                    field="options",
                    code="OPTION_LABEL_REQUIRED",
                    message=f'Option {index + 1} ("{cell}") has no label before the colon.',
                )
            )
            continue
        options.append(OptionDraft(label=label, text=text, position=index + 1))

    return options, issues


# ---------------------------------------------------------------------------
# Answer keys
# ---------------------------------------------------------------------------


def _apply_correct_answers(
    question_type: QuestionType,
    options: list[OptionDraft],
    labels: dict[str, OptionDraft],
    correct_raw: list[str],
    order_raw: list[str],
) -> list[FieldIssue]:
    issues: list[FieldIssue] = []

    if order_raw:
        issues.append(
            FieldIssue(
                field="correct_order",
                code="CORRECT_ORDER_NOT_ALLOWED",
                message=(
                    "correct_order must be empty for "
                    f"{question_type.value} questions; it is only used by DRAG_TO_ORDER."
                ),
            )
        )

    if not correct_raw:
        issues.append(
            FieldIssue(
                field="correct_answers",
                code="CORRECT_ANSWER_REQUIRED",
                message=(
                    "correct_answers is required. Give the label(s) of the correct option"
                    + (
                        ", e.g. TRUE or FALSE."
                        if question_type is QuestionType.TRUE_FALSE
                        else ", e.g. A or A|C."
                    )
                ),
            )
        )
        return issues

    seen: set[str] = set()
    for raw_label in correct_raw:
        key = raw_label.upper()
        if key in seen:
            issues.append(
                FieldIssue(
                    field="correct_answers",
                    code="DUPLICATE_CORRECT_ANSWER",
                    message=f'correct_answers lists "{raw_label}" more than once.',
                )
            )
            continue
        seen.add(key)

        option = labels.get(key)
        if option is None:
            available = ", ".join(sorted(labels)) or "none"
            if question_type is QuestionType.TRUE_FALSE:
                message = (
                    f"The correct answer for a True/False question must be "
                    f'{" or ".join(TRUE_FALSE_LABELS)} (received "{raw_label}").'
                )
            else:
                message = (
                    f'Correct answer "{raw_label}" references an option that does not exist. '
                    f"Available option labels: {available}."
                )
            issues.append(
                FieldIssue(
                    field="correct_answers",
                    code="CORRECT_ANSWER_REFERENCES_UNKNOWN_OPTION",
                    message=message,
                )
            )
            continue

        option.is_correct = True

    # SCENARIO: exactly one label, which becomes the primary answer.
    if question_type is QuestionType.SCENARIO:
        if len(correct_raw) > 1:
            issues.append(
                FieldIssue(
                    field="correct_answers",
                    code="SCENARIO_REQUIRES_SINGLE_PRIMARY_ANSWER",
                    message=(
                        "A SCENARIO row must give exactly one label in correct_answers — it is "
                        f"the primary answer (received {len(correct_raw)}: "
                        f"{', '.join(correct_raw)})."
                    ),
                )
            )
        else:
            primary = labels.get(correct_raw[0].upper())
            if primary is not None:
                primary.is_primary = True

    return issues


def _apply_drag_order(
    options: list[OptionDraft],
    labels: dict[str, OptionDraft],
    correct_raw: list[str],
    order_raw: list[str],
) -> list[FieldIssue]:
    issues: list[FieldIssue] = []

    if correct_raw:
        issues.append(
            FieldIssue(
                field="correct_answers",
                code="CORRECT_ANSWERS_NOT_ALLOWED",
                message=(
                    "correct_answers must be empty for DRAG_TO_ORDER questions. "
                    "Use correct_order to give the correct sequence of labels."
                ),
            )
        )

    if not order_raw:
        issues.append(
            FieldIssue(
                field="correct_order",
                code="CORRECT_ORDER_REQUIRED",
                message=(
                    "correct_order is required for DRAG_TO_ORDER questions. Give the option "
                    "labels in the correct sequence, e.g. A|B|C|D."
                ),
            )
        )
        return issues

    assigned: set[str] = set()
    position = 0
    for raw_label in order_raw:
        key = raw_label.upper()
        if key in assigned:
            issues.append(
                FieldIssue(
                    field="correct_order",
                    code="DRAG_TO_ORDER_DUPLICATE_POSITION",
                    message=f'correct_order lists "{raw_label}" more than once.',
                )
            )
            continue

        option = labels.get(key)
        if option is None:
            available = ", ".join(sorted(labels)) or "none"
            issues.append(
                FieldIssue(
                    field="correct_order",
                    code="CORRECT_ORDER_REFERENCES_UNKNOWN_OPTION",
                    message=(
                        f'correct_order references item "{raw_label}", which is not one of the '
                        f"options. Available item labels: {available}."
                    ),
                )
            )
            continue

        assigned.add(key)
        position += 1
        option.correct_position = position

    # Every item must appear in the correct order, otherwise the answer key is incomplete.
    missing = [option.label for option in options if option.correct_position is None]
    if missing and not any(i.code == "CORRECT_ORDER_REFERENCES_UNKNOWN_OPTION" for i in issues):
        issues.append(
            FieldIssue(
                field="correct_order",
                code="DRAG_TO_ORDER_MISSING_POSITIONS",
                message=(
                    "correct_order must list every item exactly once. Missing: "
                    + ", ".join(missing)
                    + "."
                ),
            )
        )

    return issues
