"""THE authoritative Question Bank validator (UC-02 §14).

Both entry points — the JSON admin API and the CSV bulk importer — funnel through
:func:`validate_question_draft`, so a question rejected by one is rejected identically by the
other. Nothing is written to the database unless this function returns ``ok=True``.

It is a pure function: no I/O, no database access, no ORM. The cross-row concerns that
genuinely need the database (duplicate detection, topic-id existence) live in the service
layer. Every problem found is reported, not just the first, so an admin — or a CSV row report
— sees the complete list of what needs fixing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.coercion import (
    is_blank,
    optional_trimmed,
    round4,
    to_int,
    to_number,
    trimmed,
    truthy,
)
from app.core.errors import FieldIssue
from app.modules.question_bank.domain.drafts import (
    OptionDraft,
    QuestionDraft,
    ValidatedOption,
    ValidatedQuestion,
)
from app.modules.question_bank.domain.enums import (
    ALLOWED_SCORING_STRATEGIES,
    DRAG_TO_ORDER_MIN_ITEMS,
    MAX_EXPLANATION_LENGTH,
    MAX_OPTION_TEXT_LENGTH,
    MAX_OPTIONS_PER_QUESTION,
    MAX_POINTS,
    MAX_QUESTION_TEXT_LENGTH,
    MAX_SCENARIO_TEXT_LENGTH,
    MULTI_SELECT_MIN_OPTIONS,
    SCENARIO_MIN_LENGTH,
    SCENARIO_MIN_OPTIONS,
    SINGLE_CHOICE_OPTION_COUNT,
    TRUE_FALSE_LABELS,
    Difficulty,
    QuestionStatus,
    QuestionType,
    ScoringStrategy,
    enum_values,
    parse_enum,
)
from app.modules.question_bank.domain.policy import OPTION_LABEL_PATTERN, question_policy

_TRUTHY = {"true", "yes", "y", "1", "t"}
_FALSY = {"false", "no", "n", "0", "f", ""}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ValidationOutcome:
    ok: bool
    value: ValidatedQuestion | None = None
    issues: list[FieldIssue] = field(default_factory=list)
    #: Non-fatal notes (e.g. a duplicate topic was ignored). Surfaced, never blocking.
    warnings: list[FieldIssue] = field(default_factory=list)


class _Collector:
    """Accumulates field-level issues instead of raising on the first problem."""

    def __init__(self) -> None:
        self.issues: list[FieldIssue] = []
        self.warnings: list[FieldIssue] = []

    def add(self, field_path: str, code: str, message: str) -> None:
        self.issues.append(FieldIssue(field=field_path, code=code, message=message))

    def warn(self, field_path: str, code: str, message: str) -> None:
        self.warnings.append(FieldIssue(field=field_path, code=code, message=message))

    def has_issue_under(self, prefix: str) -> bool:
        return any(issue.field.startswith(prefix) for issue in self.issues)


# ---------------------------------------------------------------------------
# Type / status
# ---------------------------------------------------------------------------


def _validate_type(draft: QuestionDraft, c: _Collector) -> QuestionType | None:
    raw = trimmed(draft.type)
    if not raw:
        c.add("type", "QUESTION_TYPE_REQUIRED", "A question type is required.")
        return None
    parsed = parse_enum(QuestionType, raw)
    if parsed is None:
        c.add(
            "type",
            "INVALID_QUESTION_TYPE",
            f'Invalid question type: "{raw}". Expected one of '
            f"{', '.join(enum_values(QuestionType))}.",
        )
        return None
    return parsed


def _validate_status(draft: QuestionDraft, c: _Collector) -> QuestionStatus:
    if is_blank(draft.status):
        return QuestionStatus.ACTIVE
    raw = trimmed(draft.status)
    parsed = parse_enum(QuestionStatus, raw)
    if parsed is None:
        c.add(
            "status",
            "INVALID_STATUS",
            f'Invalid status: "{raw}". Expected one of {", ".join(enum_values(QuestionStatus))}.',
        )
        return QuestionStatus.ACTIVE
    return parsed


# ---------------------------------------------------------------------------
# Text fields
# ---------------------------------------------------------------------------


def _validate_texts(
    draft: QuestionDraft, question_type: QuestionType | None, c: _Collector
) -> tuple[str, str | None, str | None]:
    question_text = trimmed(draft.question_text)
    if not question_text:
        c.add("questionText", "QUESTION_TEXT_REQUIRED", "Question text is required.")
    elif len(question_text) > MAX_QUESTION_TEXT_LENGTH:
        c.add(
            "questionText",
            "QUESTION_TEXT_TOO_LONG",
            f"Question text must be {MAX_QUESTION_TEXT_LENGTH} characters or fewer "
            f"(received {len(question_text)}).",
        )

    scenario_text = optional_trimmed(draft.scenario_text)
    if question_type is QuestionType.SCENARIO:
        if not scenario_text:
            c.add(
                "scenarioText",
                "SCENARIO_TEXT_REQUIRED",
                "Scenario questions require a scenario / vignette before the question.",
            )
        elif len(scenario_text) < SCENARIO_MIN_LENGTH:
            c.add(
                "scenarioText",
                "SCENARIO_TEXT_TOO_SHORT",
                f"Scenario text must be at least {SCENARIO_MIN_LENGTH} characters to form a "
                f"usable vignette (received {len(scenario_text)}).",
            )
        elif len(scenario_text) > MAX_SCENARIO_TEXT_LENGTH:
            c.add(
                "scenarioText",
                "SCENARIO_TEXT_TOO_LONG",
                f"Scenario text must be {MAX_SCENARIO_TEXT_LENGTH} characters or fewer "
                f"(received {len(scenario_text)}).",
            )
    elif scenario_text and question_type is not None:
        c.add(
            "scenarioText",
            "SCENARIO_TEXT_NOT_ALLOWED",
            f"Scenario text is only valid for SCENARIO questions, not {question_type.value}.",
        )
        scenario_text = None

    explanation = optional_trimmed(draft.explanation)
    if question_policy.require_explanation and not explanation:
        c.add(
            "explanation",
            "EXPLANATION_REQUIRED",
            "An explanation is required for every question.",
        )
    elif explanation and len(explanation) > MAX_EXPLANATION_LENGTH:
        c.add(
            "explanation",
            "EXPLANATION_TOO_LONG",
            f"Explanation must be {MAX_EXPLANATION_LENGTH} characters or fewer "
            f"(received {len(explanation)}).",
        )

    if question_type is not QuestionType.SCENARIO:
        scenario_text = None

    return question_text, scenario_text, explanation


def _validate_difficulty(draft: QuestionDraft, c: _Collector) -> Difficulty | None:
    raw = optional_trimmed(draft.difficulty)
    if raw is None:
        return None
    parsed = parse_enum(Difficulty, raw)
    if parsed is None:
        c.add(
            "difficulty",
            "INVALID_DIFFICULTY",
            f'Invalid difficulty: "{raw}". Expected one of {", ".join(enum_values(Difficulty))}.',
        )
        return None
    return parsed


# ---------------------------------------------------------------------------
# Scoring metadata
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Scoring:
    points: float
    scoring_strategy: ScoringStrategy
    penalty_per_incorrect: float


def _validate_scoring(
    draft: QuestionDraft, question_type: QuestionType | None, c: _Collector
) -> _Scoring:
    scoring = draft.scoring

    # ---- points ----
    points = 1.0
    if not is_blank(scoring.points):
        parsed = to_number(scoring.points)
        if parsed is None:
            c.add(
                "scoring.points",
                "INVALID_POINTS",
                f'Scoring points must be a number (received "{scoring.points}").',
            )
        elif parsed <= 0:
            c.add(
                "scoring.points",
                "POINTS_MUST_BE_POSITIVE",
                "Scoring points must be greater than zero.",
            )
        elif parsed > MAX_POINTS:
            c.add(
                "scoring.points",
                "POINTS_TOO_LARGE",
                f"Scoring points must not exceed {MAX_POINTS} (received {parsed:g}).",
            )
        else:
            points = round4(parsed)

    # ---- strategy ----
    strategy = ScoringStrategy.ALL_OR_NOTHING
    raw_strategy = optional_trimmed(scoring.scoring_strategy)
    if raw_strategy is not None:
        parsed_strategy = parse_enum(ScoringStrategy, raw_strategy)
        if parsed_strategy is None:
            c.add(
                "scoring.scoringStrategy",
                "INVALID_SCORING_STRATEGY",
                f'Invalid scoring strategy: "{raw_strategy}". Expected one of '
                f"{', '.join(enum_values(ScoringStrategy))}.",
            )
        elif (
            question_type is not None
            and parsed_strategy not in ALLOWED_SCORING_STRATEGIES[question_type]
        ):
            allowed = ", ".join(s.value for s in ALLOWED_SCORING_STRATEGIES[question_type])
            c.add(
                "scoring.scoringStrategy",
                "SCORING_STRATEGY_NOT_ALLOWED_FOR_TYPE",
                f"Scoring strategy {parsed_strategy.value} is not valid for "
                f"{question_type.value} questions. Allowed: {allowed}.",
            )
        else:
            strategy = parsed_strategy

    # ---- penalty ----
    penalty = 0.0
    if not is_blank(scoring.penalty_per_incorrect):
        parsed_penalty = to_number(scoring.penalty_per_incorrect)
        if parsed_penalty is None:
            c.add(
                "scoring.penaltyPerIncorrect",
                "INVALID_PENALTY",
                f"Penalty per incorrect selection must be a number "
                f'(received "{scoring.penalty_per_incorrect}").',
            )
        elif parsed_penalty < 0:
            c.add(
                "scoring.penaltyPerIncorrect",
                "PENALTY_MUST_NOT_BE_NEGATIVE",
                "Penalty per incorrect selection must not be negative.",
            )
        else:
            penalty = round4(parsed_penalty)

    if strategy is ScoringStrategy.PARTIAL_CREDIT_WITH_PENALTY and penalty <= 0:
        c.add(
            "scoring.penaltyPerIncorrect",
            "PENALTY_REQUIRED_FOR_STRATEGY",
            "Scoring strategy PARTIAL_CREDIT_WITH_PENALTY requires a penaltyPerIncorrect "
            "greater than zero.",
        )
    if strategy is not ScoringStrategy.PARTIAL_CREDIT_WITH_PENALTY and penalty > 0:
        c.add(
            "scoring.penaltyPerIncorrect",
            "PENALTY_NOT_ALLOWED_FOR_STRATEGY",
            "penaltyPerIncorrect must be 0 unless the scoring strategy is "
            f"PARTIAL_CREDIT_WITH_PENALTY (strategy is {strategy.value}).",
        )
    if penalty > points:
        c.add(
            "scoring.penaltyPerIncorrect",
            "PENALTY_EXCEEDS_POINTS",
            f"penaltyPerIncorrect ({penalty:g}) must not exceed the question's points "
            f"({points:g}).",
        )

    return _Scoring(points=points, scoring_strategy=strategy, penalty_per_incorrect=penalty)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def _synthesise_true_false_options() -> list[OptionDraft]:
    """The TRUE/FALSE option pair is fixed by definition."""
    return [
        OptionDraft(label="TRUE", text="True", position=1, is_correct=False),
        OptionDraft(label="FALSE", text="False", position=2, is_correct=False),
    ]


def _normalise_options(
    draft: QuestionDraft, question_type: QuestionType | None, c: _Collector
) -> list[ValidatedOption]:
    raw = list(draft.options or [])

    if question_type is QuestionType.TRUE_FALSE and not raw:
        # A True/False question is still invalid without a stated answer, but synthesising the
        # fixed pair produces a far clearer error than "options required".
        raw = _synthesise_true_false_options()
        c.add(
            "options.correct",
            "TRUE_FALSE_ANSWER_REQUIRED",
            "A True/False question must state whether the correct answer is TRUE or FALSE.",
        )

    if not raw:
        c.add("options", "OPTIONS_REQUIRED", "At least one answer option is required.")
        return []

    if len(raw) > MAX_OPTIONS_PER_QUESTION:
        c.add(
            "options",
            "TOO_MANY_OPTIONS",
            f"A question may not have more than {MAX_OPTIONS_PER_QUESTION} options "
            f"(received {len(raw)}).",
        )
        return []

    seen_labels: dict[str, int] = {}
    options: list[ValidatedOption] = []

    for index, option in enumerate(raw):
        path = f"options[{index}]"
        label = trimmed(option.label)
        text = trimmed(option.text)

        if not label:
            c.add(
                f"{path}.label", "OPTION_LABEL_REQUIRED", f"Option {index + 1} is missing a label."
            )
        elif len(label) > question_policy.max_option_label_length:
            c.add(
                f"{path}.label",
                "OPTION_LABEL_TOO_LONG",
                f'Option label "{label}" exceeds '
                f"{question_policy.max_option_label_length} characters.",
            )
        elif not OPTION_LABEL_PATTERN.match(label):
            c.add(
                f"{path}.label",
                "OPTION_LABEL_INVALID",
                f'Option label "{label}" is invalid. Use letters, digits, "-", "_" or "." '
                "and start with a letter or digit.",
            )
        else:
            key = label.upper()
            previous = seen_labels.get(key)
            if previous is not None:
                c.add(
                    f"{path}.label",
                    "DUPLICATE_OPTION_LABEL",
                    f'Duplicate option label "{label}" (also used by option {previous + 1}). '
                    "Option labels must be unique within a question.",
                )
            else:
                seen_labels[key] = index

        if not text:
            c.add(
                f"{path}.text",
                "OPTION_TEXT_REQUIRED",
                f"Option {label or index + 1} is missing text.",
            )
        elif len(text) > MAX_OPTION_TEXT_LENGTH:
            c.add(
                f"{path}.text",
                "OPTION_TEXT_TOO_LONG",
                f"Option {label or index + 1} text exceeds {MAX_OPTION_TEXT_LENGTH} characters.",
            )

        correct_position: int | None = None
        if not is_blank(option.correct_position):
            parsed = to_int(option.correct_position)
            if parsed is None:
                c.add(
                    f"{path}.correctPosition",
                    "INVALID_CORRECT_POSITION",
                    f"Option {label or index + 1} has a non-integer correct position "
                    f'("{option.correct_position}").',
                )
            else:
                correct_position = parsed

        position = to_int(option.position)
        options.append(
            ValidatedOption(
                label=label,
                text=text,
                position=position if position is not None else index + 1,
                is_correct=truthy(option.is_correct),
                is_primary=truthy(option.is_primary),
                correct_position=correct_position,
                feedback=optional_trimmed(option.feedback),
            )
        )

    # Normalise presentation positions to a dense, deterministic 1..n sequence while
    # preserving the caller's intended relative order.
    options.sort(key=lambda o: o.position)
    for index, option in enumerate(options):
        option.position = index + 1

    return options


# ---------------------------------------------------------------------------
# Type-specific rules (UC-02 §9–§14)
# ---------------------------------------------------------------------------


def _reject_correct_positions(
    options: list[ValidatedOption], question_type: QuestionType, c: _Collector
) -> None:
    """``correct_position`` is only meaningful for DRAG_TO_ORDER; reject it elsewhere."""
    offenders = [o for o in options if o.correct_position is not None]
    if offenders:
        c.add(
            "options.correctPosition",
            "CORRECT_POSITION_NOT_ALLOWED",
            "Correct order positions are only valid for DRAG_TO_ORDER questions, not "
            f"{question_type.value} (set on: {', '.join(o.label for o in offenders)}).",
        )
        for option in offenders:
            option.correct_position = None


def _validate_single_choice(options: list[ValidatedOption], c: _Collector) -> None:
    if len(options) != SINGLE_CHOICE_OPTION_COUNT:
        c.add(
            "options",
            "SINGLE_CHOICE_REQUIRES_FOUR_OPTIONS",
            f"Single-choice questions require exactly {SINGLE_CHOICE_OPTION_COUNT} answer "
            f"options (received {len(options)}).",
        )
    correct = [o for o in options if o.is_correct]
    if len(correct) != 1:
        c.add(
            "options.correct",
            "SINGLE_CHOICE_REQUIRES_ONE_CORRECT",
            "Single-choice question requires exactly one correct answer "
            f"(received {len(correct)}).",
        )
    _reject_correct_positions(options, QuestionType.SINGLE_CHOICE, c)
    # The single correct option is implicitly the primary answer.
    for option in options:
        option.is_primary = option.is_correct and len(correct) == 1


def _validate_true_false(options: list[ValidatedOption], c: _Collector) -> None:
    labels = [o.label.upper() for o in options]
    if len(options) != 2 or sorted(labels) != sorted(TRUE_FALSE_LABELS):
        received = ", ".join(labels) if labels else "none"
        c.add(
            "options",
            "TRUE_FALSE_REQUIRES_TRUE_FALSE_OPTIONS",
            "True/False questions must have exactly two options labelled "
            f"{' and '.join(TRUE_FALSE_LABELS)} (received {received}).",
        )

    correct = [o for o in options if o.is_correct]
    if len(correct) != 1:
        if not c.has_issue_under("options.correct"):
            c.add(
                "options.correct",
                "TRUE_FALSE_REQUIRES_ONE_CORRECT",
                "A True/False question must have exactly one correct answer, TRUE or FALSE "
                f"(received {len(correct)}).",
            )
    elif correct[0].label.upper() not in TRUE_FALSE_LABELS:
        c.add(
            "options.correct",
            "TRUE_FALSE_INVALID_ANSWER",
            "The correct answer for a True/False question must be TRUE or FALSE "
            f'(received "{correct[0].label}").',
        )

    _reject_correct_positions(options, QuestionType.TRUE_FALSE, c)
    for option in options:
        option.is_primary = option.is_correct and len(correct) == 1


def _validate_multi_select(
    options: list[ValidatedOption], scoring: _Scoring, c: _Collector
) -> None:
    if len(options) < MULTI_SELECT_MIN_OPTIONS:
        c.add(
            "options",
            "MULTI_SELECT_REQUIRES_MIN_OPTIONS",
            f"Multi-select questions require at least {MULTI_SELECT_MIN_OPTIONS} answer "
            f"options (received {len(options)}).",
        )
    correct = [o for o in options if o.is_correct]
    if not correct:
        c.add(
            "options.correct",
            "MULTI_SELECT_REQUIRES_CORRECT_ANSWER",
            "Multi-select question requires at least one correct answer.",
        )
    elif len(correct) == len(options):
        c.add(
            "options.correct",
            "MULTI_SELECT_ALL_OPTIONS_CORRECT",
            "Multi-select question cannot mark every option correct; at least one distractor "
            "is required.",
        )
    # Partial credit is only meaningful when the marks can actually be divided.
    if scoring.scoring_strategy is not ScoringStrategy.ALL_OR_NOTHING and len(correct) < 2:
        c.add(
            "scoring.scoringStrategy",
            "PARTIAL_CREDIT_REQUIRES_MULTIPLE_CORRECT",
            f"Scoring strategy {scoring.scoring_strategy.value} requires at least two correct "
            f"answers to divide marks between (received {len(correct)}).",
        )
    _reject_correct_positions(options, QuestionType.MULTI_SELECT, c)
    for option in options:
        option.is_primary = False


def _validate_scenario(options: list[ValidatedOption], c: _Collector) -> None:
    if len(options) < SCENARIO_MIN_OPTIONS:
        c.add(
            "options",
            "SCENARIO_REQUIRES_MIN_OPTIONS",
            f"Scenario questions require at least {SCENARIO_MIN_OPTIONS} answer options "
            f"(received {len(options)}).",
        )

    correct = [o for o in options if o.is_correct]
    if not correct:
        c.add(
            "options.correct",
            "SCENARIO_REQUIRES_CORRECT_ANSWER",
            "Scenario question requires a correct answer.",
        )

    primaries = [o for o in options if o.is_primary]
    # Convenience: with a single correct option and no explicit primary flag, that option is
    # unambiguously the primary answer.
    if not primaries and len(correct) == 1:
        correct[0].is_primary = True
        primaries = [correct[0]]

    if not primaries:
        c.add(
            "options.primary",
            "SCENARIO_PRIMARY_ANSWER_REQUIRED",
            "Scenario question requires exactly one primary answer; none was marked.",
        )
    elif len(primaries) > 1:
        c.add(
            "options.primary",
            "SCENARIO_MULTIPLE_PRIMARY_ANSWERS",
            f"Scenario question requires exactly one primary answer (received "
            f"{len(primaries)}: {', '.join(o.label for o in primaries)}).",
        )
    elif not primaries[0].is_correct:
        c.add(
            "options.primary",
            "SCENARIO_PRIMARY_ANSWER_NOT_CORRECT",
            f'The primary answer "{primaries[0].label}" must also be marked as a correct answer.',
        )

    _reject_correct_positions(options, QuestionType.SCENARIO, c)


def _validate_drag_to_order(options: list[ValidatedOption], c: _Collector) -> None:
    if len(options) < DRAG_TO_ORDER_MIN_ITEMS:
        c.add(
            "options",
            "DRAG_TO_ORDER_REQUIRES_MIN_ITEMS",
            f"Drag-to-order questions require at least {DRAG_TO_ORDER_MIN_ITEMS} ordered "
            f"items (received {len(options)}).",
        )
        return

    # Item text must be unique — two identical items make the correct order ambiguous.
    seen_text: dict[str, str] = {}
    for option in options:
        key = option.text.lower()
        if not key:
            continue
        previous = seen_text.get(key)
        if previous is not None:
            c.add(
                "options",
                "DRAG_TO_ORDER_DUPLICATE_ITEM",
                f'Ordered items must be unique: "{option.text}" appears as both '
                f"{previous} and {option.label}.",
            )
        else:
            seen_text[key] = option.label

    missing = [o for o in options if o.correct_position is None]
    if missing:
        c.add(
            "options.correctPosition",
            "DRAG_TO_ORDER_MISSING_POSITIONS",
            "Every ordered item needs a correct position. Missing for: "
            f"{', '.join(o.label for o in missing)}.",
        )
    else:
        positions = sorted(o.correct_position for o in options if o.correct_position is not None)
        expected = list(range(1, len(options) + 1))
        if positions != expected:
            duplicates = sorted({p for i, p in enumerate(positions) if i and p == positions[i - 1]})
            if duplicates:
                c.add(
                    "options.correctPosition",
                    "DRAG_TO_ORDER_DUPLICATE_POSITION",
                    "Correct order positions must be unique; duplicated position(s): "
                    f"{', '.join(str(p) for p in duplicates)}.",
                )
            else:
                c.add(
                    "options.correctPosition",
                    "DRAG_TO_ORDER_INVALID_SEQUENCE",
                    f"Correct order must be a complete sequence from 1 to {len(options)} "
                    f"(received {', '.join(str(p) for p in positions)}).",
                )

    # Correctness for an ordering question lives entirely in `correct_position`.
    for option in options:
        option.is_correct = False
        option.is_primary = False


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


def _validate_topics(draft: QuestionDraft, c: _Collector) -> tuple[list[str], list[str]]:
    topic_names: list[str] = []
    seen: set[str] = set()

    for index, value in enumerate(draft.topics or []):
        name = trimmed(value)
        if not name:
            c.add(f"topics[{index}]", "TOPIC_NAME_REQUIRED", f"Topic {index + 1} is empty.")
            continue
        if len(name) > question_policy.max_topic_name_length:
            c.add(
                f"topics[{index}]",
                "TOPIC_NAME_TOO_LONG",
                f'Topic "{name}" exceeds {question_policy.max_topic_name_length} characters.',
            )
            continue
        key = name.lower()
        if key in seen:
            c.warn(f"topics[{index}]", "DUPLICATE_TOPIC", f'Duplicate topic "{name}" was ignored.')
            continue
        seen.add(key)
        topic_names.append(name)

    topic_ids: list[str] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(draft.topic_ids or []):
        topic_id = trimmed(value)
        if not topic_id:
            c.add(f"topicIds[{index}]", "TOPIC_ID_REQUIRED", f"Topic id {index + 1} is empty.")
            continue
        if topic_id in seen_ids:
            continue
        seen_ids.add(topic_id)
        topic_ids.append(topic_id)

    total = len(topic_names) + len(topic_ids)
    if question_policy.require_at_least_one_topic and total == 0:
        c.add("topics", "TOPICS_REQUIRED", "At least one topic must be assigned to the question.")
    if total > question_policy.max_topics_per_question:
        c.add(
            "topics",
            "TOO_MANY_TOPICS",
            f"A question may not have more than {question_policy.max_topics_per_question} "
            f"topics (received {total}).",
        )

    return topic_names, topic_ids


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def validate_question_draft(draft: QuestionDraft) -> ValidationOutcome:
    """Validate and normalise a question draft.

    Returns a :class:`ValidationOutcome`; when ``ok`` is False, ``issues`` lists every
    field-level problem found.
    """
    c = _Collector()

    question_type = _validate_type(draft, c)
    status = _validate_status(draft, c)
    question_text, scenario_text, explanation = _validate_texts(draft, question_type, c)
    difficulty = _validate_difficulty(draft, c)
    scoring = _validate_scoring(draft, question_type, c)
    topic_names, topic_ids = _validate_topics(draft, c)
    options = _normalise_options(draft, question_type, c)

    if question_type is not None and options:
        if question_type is QuestionType.SINGLE_CHOICE:
            _validate_single_choice(options, c)
        elif question_type is QuestionType.TRUE_FALSE:
            _validate_true_false(options, c)
        elif question_type is QuestionType.MULTI_SELECT:
            _validate_multi_select(options, scoring, c)
        elif question_type is QuestionType.SCENARIO:
            _validate_scenario(options, c)
        elif question_type is QuestionType.DRAG_TO_ORDER:
            _validate_drag_to_order(options, c)

        # General rule (UC-02 §14): a question must always define a correct answer.
        if question_type is QuestionType.DRAG_TO_ORDER:
            has_correct = all(o.correct_position is not None for o in options)
        else:
            has_correct = any(o.is_correct for o in options)
        if not has_correct and not c.has_issue_under("options"):
            c.add("options.correct", "CORRECT_ANSWER_REQUIRED", "A correct answer is required.")

    if c.issues:
        return ValidationOutcome(ok=False, issues=c.issues, warnings=c.warnings)

    assert question_type is not None  # guaranteed: a missing type produces an issue above
    return ValidationOutcome(
        ok=True,
        warnings=c.warnings,
        value=ValidatedQuestion(
            type=question_type,
            status=status,
            question_text=question_text,
            scenario_text=scenario_text,
            explanation=explanation,
            difficulty=difficulty,
            external_ref=optional_trimmed(draft.external_ref),
            points=scoring.points,
            scoring_strategy=scoring.scoring_strategy,
            penalty_per_incorrect=scoring.penalty_per_incorrect,
            options=options,
            topic_names=topic_names,
            topic_ids=topic_ids,
        ),
    )
