"""Authoritative quiz-configuration rules and pure validation.

This module is the single source of truth for the UC-01 product rules. The admin UI mirrors
them for instant feedback (``frontend/src/lib/configurationRules.ts``), but nothing is ever
persisted without passing the checks in here — the mirror is a convenience, this is the gate.

Question-bank capacity is validated by :func:`evaluate_capacity`, which takes availability as an
argument rather than reading the bank itself. That keeps this module pure and means the admin's
pre-save warning and the server's authoritative answer run identical arithmetic.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.coercion import is_blank, parse_enum, to_int
from app.core.errors import FieldIssue
from app.core.question_types import QuestionPresentation
from app.modules.quiz_configuration.domain.enums import (
    QUESTION_TYPE_LABELS,
    QUESTION_TYPE_ORDER,
    DeliveryMode,
    QuestionType,
    delivery_mode_requires_time_limit,
)

#: Numeric bounds enforced here, mirrored in the UI, and re-asserted by database CHECK
#: constraints so a direct write cannot bypass them either.
LIMITS: dict[str, dict[str, int]] = {
    "questionCount": {"min": 1, "max": 100},
    "timeLimitMinutes": {"min": 1, "max": 480},
    "passMark": {"min": 1, "max": 100},
    "maxAttempts": {"min": 1, "max": 50},
    "questionQuota": {"min": 1, "max": 100},
}

#: A configuration may scope its eligible pool to at most this many topics.
MAX_CONFIGURATION_TOPICS = 20


class ValidationCode(StrEnum):
    """Machine-readable reasons a configuration was rejected.

    Field errors from this module share the shape used by the question validator
    (``{field, code, message}``), so a client renders both the same way.
    """

    REQUIRED = "REQUIRED"
    NOT_AN_INTEGER = "NOT_AN_INTEGER"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    INVALID_VALUE = "INVALID_VALUE"
    INVALID_QUESTION_TYPE = "INVALID_QUESTION_TYPE"
    DUPLICATE_QUESTION_TYPE = "DUPLICATE_QUESTION_TYPE"
    NO_QUESTION_TYPE_SELECTED = "NO_QUESTION_TYPE_SELECTED"
    QUOTA_SHAPE = "QUOTA_SHAPE"
    QUOTA_SUM_MISMATCH = "QUOTA_SUM_MISMATCH"
    TIME_LIMIT_REQUIRED = "TIME_LIMIT_REQUIRED"
    TOO_MANY_TOPICS = "TOO_MANY_TOPICS"
    NOT_AN_OBJECT = "NOT_AN_OBJECT"


@dataclass(frozen=True, slots=True)
class QuestionTypeSelection:
    """One selected question type.

    ``quota`` is how many questions must come from this type. Either every selected type carries
    a quota (and the quotas add up to ``question_count``), or none do — in which case questions
    are drawn freely across the selected types.
    """

    type: QuestionType
    quota: int | None


@dataclass(frozen=True, slots=True)
class QuizConfiguration:
    """A validated, normalised configuration ready to be versioned."""

    question_count: int
    time_limit_minutes: int | None
    pass_mark: int
    question_types: tuple[QuestionTypeSelection, ...]
    randomise_questions: bool
    max_attempts: int
    delivery_mode: DeliveryMode
    #: Optional topic scope. Empty means "the whole active question bank is eligible".
    #: Topic ids are the question bank's own identifiers; see ``ConfigurationScope``.
    topic_ids: tuple[str, ...] = ()

    # ---- delivery settings read by UC-03 ---------------------------------
    #: How the questions are handed to the learner. Distinct from ``delivery_mode``, which is
    #: about grading and feedback; see :class:`app.core.question_types.QuestionPresentation`.
    question_presentation: QuestionPresentation = QuestionPresentation.ALL_AT_ONCE
    #: Shuffle the options/items *within* a question, independently of question order.
    randomise_option_order: bool = False
    #: May the learner submit with questions left unanswered?
    allow_incomplete_submission: bool = True

    @property
    def selected_types(self) -> tuple[QuestionType, ...]:
        return tuple(entry.type for entry in self.question_types)

    @property
    def uses_quotas(self) -> bool:
        """True when every selected type carries an explicit per-type quota."""
        return bool(self.question_types) and all(
            entry.quota is not None for entry in self.question_types
        )


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    errors: list[FieldIssue] = field(default_factory=list)
    value: QuizConfiguration | None = None


_MISSING = object()


def _absent(value: Any) -> bool:
    """Blank, or the "key was not supplied at all" sentinel.

    Wraps the shared :func:`app.core.coercion.is_blank` with the one thing local to reading a
    configuration payload: telling an omitted key from an explicit ``null``.
    """
    return value is _MISSING or is_blank(value)


def sort_question_types(
    selections: Iterable[QuestionTypeSelection],
) -> tuple[QuestionTypeSelection, ...]:
    return tuple(sorted(selections, key=lambda entry: QUESTION_TYPE_ORDER.index(entry.type)))


def _bounded_int(
    raw: Any,
    *,
    field_name: str,
    label: str,
    bounds: dict[str, int],
    required: bool,
    unit: str = "",
    range_suffix: str = "",
    push,
) -> int | None:
    """Shared parse-and-range-check for every numeric setting.

    ``unit`` and ``range_suffix`` decorate the *range* message only ("between 1 and 480 minutes,
    or leave empty for no limit"); the "must be a whole number" message never takes a unit,
    because "a whole number%" reads as nonsense.
    """
    if _absent(raw):
        if required:
            push(field_name, ValidationCode.REQUIRED, f"{label} is required.")
        return None

    parsed = to_int(raw)
    if parsed is None:
        push(field_name, ValidationCode.NOT_AN_INTEGER, f"{label} must be a whole number.")
        return None

    if not bounds["min"] <= parsed <= bounds["max"]:
        push(
            field_name,
            ValidationCode.OUT_OF_RANGE,
            f"{label} must be between {bounds['min']} and {bounds['max']}{unit}{range_suffix}.",
        )
        return None

    return parsed


def validate_configuration(raw: Any) -> ValidationResult:
    """Validate a configuration payload against the UC-01 product rules.

    Collects *every* problem rather than failing on the first, so an administrator can fix the
    whole form in one pass. Question-bank capacity is deliberately not covered here — that needs
    the bank and lives in :func:`evaluate_capacity`.
    """
    if not isinstance(raw, Mapping):
        return ValidationResult(
            valid=False,
            errors=[
                FieldIssue(
                    "_root", ValidationCode.NOT_AN_OBJECT, "A configuration object is required."
                )
            ],
        )

    errors: list[FieldIssue] = []

    def push(field_name: str, code: str, message: str) -> None:
        errors.append(FieldIssue(field_name, str(code), message))

    question_count = _bounded_int(
        raw.get("questionCount", _MISSING),
        field_name="questionCount",
        label="Question count",
        bounds=LIMITS["questionCount"],
        required=True,
        push=push,
    )

    # A blank time limit means "no limit", so it is optional unless the delivery mode says
    # otherwise (checked in the cross-field section below).
    time_limit = _bounded_int(
        raw.get("timeLimitMinutes", _MISSING),
        field_name="timeLimitMinutes",
        label="Time limit",
        bounds=LIMITS["timeLimitMinutes"],
        required=False,
        unit=" minutes",
        range_suffix=", or left empty for no limit",
        push=push,
    )

    pass_mark = _bounded_int(
        raw.get("passMark", _MISSING),
        field_name="passMark",
        label="Pass mark",
        bounds=LIMITS["passMark"],
        required=True,
        unit="%",
        push=push,
    )

    max_attempts = _bounded_int(
        raw.get("maxAttempts", _MISSING),
        field_name="maxAttempts",
        label="Maximum attempts",
        bounds=LIMITS["maxAttempts"],
        required=True,
        push=push,
    )

    # --- delivery mode ------------------------------------------------------
    delivery_mode: DeliveryMode | None = None
    raw_delivery_mode = raw.get("deliveryMode", _MISSING)
    if _absent(raw_delivery_mode):
        push("deliveryMode", ValidationCode.REQUIRED, "Delivery mode is required.")
    else:
        delivery_mode = _parse_delivery_mode(raw_delivery_mode)
        if delivery_mode is None:
            supported = ", ".join(mode.value for mode in DeliveryMode)
            push(
                "deliveryMode",
                ValidationCode.INVALID_VALUE,
                f"Delivery mode must be one of: {supported}.",
            )

    # --- randomisation ------------------------------------------------------
    randomise = False
    raw_randomise = raw.get("randomiseQuestions", _MISSING)
    if isinstance(raw_randomise, bool):
        randomise = raw_randomise
    elif raw_randomise in ("true", "false"):
        randomise = raw_randomise == "true"
    elif raw_randomise is not _MISSING and raw_randomise is not None:
        push(
            "randomiseQuestions",
            ValidationCode.INVALID_VALUE,
            "Randomisation must be either enabled or disabled.",
        )

    selections = _validate_question_types(raw, question_count, push)
    topic_ids = _validate_topics(raw, push)

    # --- delivery settings read by UC-03 ------------------------------------
    presentation = QuestionPresentation.ALL_AT_ONCE
    raw_presentation = raw.get("questionPresentation", _MISSING)
    if not _absent(raw_presentation):
        parsed_presentation = parse_enum(QuestionPresentation, raw_presentation)
        if parsed_presentation is None:
            supported = ", ".join(item.value for item in QuestionPresentation)
            push(
                "questionPresentation",
                ValidationCode.INVALID_VALUE,
                f"Question presentation must be one of: {supported}.",
            )
        else:
            presentation = parsed_presentation

    randomise_options = _optional_flag(
        raw.get("randomiseOptionOrder", _MISSING),
        field_name="randomiseOptionOrder",
        label="Option randomisation",
        default=False,
        push=push,
    )
    allow_incomplete = _optional_flag(
        raw.get("allowIncompleteSubmission", _MISSING),
        field_name="allowIncompleteSubmission",
        label="Incomplete submission",
        default=True,
        push=push,
    )

    # --- cross-field rules --------------------------------------------------
    if (
        delivery_mode is not None
        and delivery_mode_requires_time_limit(delivery_mode)
        and time_limit is None
    ):
        push(
            "timeLimitMinutes",
            ValidationCode.TIME_LIMIT_REQUIRED,
            'A time limit is required when the delivery mode is "exam".',
        )

    if errors:
        return ValidationResult(valid=False, errors=errors)

    # Unreachable unless a required field slipped through without an error being recorded.
    assert question_count is not None and pass_mark is not None
    assert max_attempts is not None and delivery_mode is not None

    return ValidationResult(
        valid=True,
        errors=[],
        value=QuizConfiguration(
            question_count=question_count,
            time_limit_minutes=time_limit,
            pass_mark=pass_mark,
            question_types=sort_question_types(selections),
            randomise_questions=randomise,
            max_attempts=max_attempts,
            delivery_mode=delivery_mode,
            topic_ids=topic_ids,
            question_presentation=presentation,
            randomise_option_order=randomise_options,
            allow_incomplete_submission=allow_incomplete,
        ),
    )


def _optional_flag(raw: Any, *, field_name: str, label: str, default: bool, push) -> bool:
    """Parse an optional on/off setting, accepting the strings an HTML form produces."""
    if _absent(raw):
        return default
    if isinstance(raw, bool):
        return raw
    if raw in ("true", "false"):
        return raw == "true"
    push(
        field_name,
        ValidationCode.INVALID_VALUE,
        f"{label} must be either enabled or disabled.",
    )
    return default


def _parse_delivery_mode(raw: Any) -> DeliveryMode | None:
    """Delivery modes are lower-case; accept any casing the way question types are accepted."""
    if not isinstance(raw, str):
        return None
    try:
        return DeliveryMode(raw.strip().lower())
    except ValueError:
        return None


def _validate_question_types(
    raw: Mapping[str, Any], question_count: int | None, push
) -> list[QuestionTypeSelection]:
    """Parse and check the question-type selection, including the quota rules."""
    selections: list[QuestionTypeSelection] = []
    raw_types = raw.get("questionTypes", _MISSING)

    if not isinstance(raw_types, Sequence) or isinstance(raw_types, (str, bytes)) or not raw_types:
        push(
            "questionTypes",
            ValidationCode.NO_QUESTION_TYPE_SELECTED,
            "Select at least one question type.",
        )
        return selections

    seen: set[QuestionType] = set()
    quota_shape_error = False

    for entry in raw_types:
        # Accept both "SINGLE_CHOICE" and {"type": "SINGLE_CHOICE", "quota": 10}.
        if isinstance(entry, Mapping):
            raw_type = entry.get("type")
            raw_quota = entry.get("quota")
        else:
            raw_type = entry
            raw_quota = None

        question_type = parse_enum(QuestionType, raw_type)
        if question_type is None:
            supported = ", ".join(item.value for item in QuestionType)
            push(
                "questionTypes",
                ValidationCode.INVALID_QUESTION_TYPE,
                f'"{raw_type}" is not a supported question type. Supported types: {supported}.',
            )
            continue

        if question_type in seen:
            push(
                "questionTypes",
                ValidationCode.DUPLICATE_QUESTION_TYPE,
                f"{QUESTION_TYPE_LABELS[question_type]} is selected more than once.",
            )
            continue
        seen.add(question_type)

        quota: int | None = None
        if not _absent(raw_quota):
            label = QUESTION_TYPE_LABELS[question_type]
            field_name = f"questionTypes.{question_type.value}.quota"
            parsed = to_int(raw_quota)
            if parsed is None:
                push(
                    field_name,
                    ValidationCode.NOT_AN_INTEGER,
                    "Question quota must be a whole number.",
                )
                quota_shape_error = True
                continue
            if parsed < LIMITS["questionQuota"]["min"]:
                push(
                    field_name,
                    ValidationCode.OUT_OF_RANGE,
                    f"Quota for {label} must be at least {LIMITS['questionQuota']['min']}.",
                )
                quota_shape_error = True
                continue
            quota = parsed

        selections.append(QuestionTypeSelection(question_type, quota))

    if quota_shape_error or not selections:
        return selections

    # Quotas are all-or-nothing and, when present, must add up to the question count.
    with_quota = [entry for entry in selections if entry.quota is not None]
    if with_quota and len(with_quota) != len(selections):
        missing = ", ".join(
            QUESTION_TYPE_LABELS[entry.type] for entry in selections if entry.quota is None
        )
        push(
            "questionTypes",
            ValidationCode.QUOTA_SHAPE,
            "Set a per-type quota for every selected type or none at all. "
            f"Missing: {missing}.",
        )
    elif len(with_quota) == len(selections) and question_count is not None:
        total = sum(entry.quota or 0 for entry in with_quota)
        if total != question_count:
            push(
                "questionTypes",
                ValidationCode.QUOTA_SUM_MISMATCH,
                f"Per-type quotas add up to {total} but the quiz is configured for "
                f"{question_count} questions.",
            )

    return selections


def _validate_topics(raw: Mapping[str, Any], push) -> tuple[str, ...]:
    """Optional topic scope. Absent or empty means the whole active bank is eligible."""
    raw_topics = raw.get("topicIds", _MISSING)
    if _absent(raw_topics) or raw_topics is _MISSING:
        return ()
    if not isinstance(raw_topics, Sequence) or isinstance(raw_topics, (str, bytes)):
        push("topicIds", ValidationCode.INVALID_VALUE, "Topic scope must be a list of topic ids.")
        return ()

    ids: list[str] = []
    for item in raw_topics:
        if not isinstance(item, str) or not item.strip():
            push(
                "topicIds",
                ValidationCode.INVALID_VALUE,
                "Every topic id must be a non-empty string.",
            )
            continue
        value = item.strip()
        if value not in ids:
            ids.append(value)

    if len(ids) > MAX_CONFIGURATION_TOPICS:
        push(
            "topicIds",
            ValidationCode.TOO_MANY_TOPICS,
            f"A configuration may scope to at most {MAX_CONFIGURATION_TOPICS} topics.",
        )
        return tuple(ids[:MAX_CONFIGURATION_TOPICS])

    return tuple(ids)


# ---------------------------------------------------------------------------
# Question-bank capacity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapacityEntry:
    type: QuestionType
    requested: int | None
    available: int
    shortfall: int


@dataclass(frozen=True, slots=True)
class CapacityReport:
    satisfiable: bool
    requested_total: int
    available_total: int
    total_shortfall: int
    breakdown: tuple[CapacityEntry, ...]
    messages: tuple[str, ...]


def evaluate_capacity(
    config: QuizConfiguration,
    available_by_type: Mapping[QuestionType, int],
) -> CapacityReport:
    """Compare a requested configuration against eligible question-bank availability.

    ``available_by_type`` must already exclude everything that cannot be delivered — retired and
    draft questions, and anything outside the configuration's topic scope. That exclusion is the
    question bank's job (see ``QuestionBankPort``), so this function stays pure arithmetic.
    """
    uses_quotas = config.uses_quotas

    breakdown: list[CapacityEntry] = []
    for selection in config.question_types:
        available = int(available_by_type.get(selection.type, 0))
        requested = selection.quota if uses_quotas else None
        shortfall = max(0, requested - available) if requested is not None else 0
        breakdown.append(CapacityEntry(selection.type, requested, available, shortfall))

    available_total = sum(entry.available for entry in breakdown)
    messages: list[str] = []
    satisfiable = True

    if uses_quotas:
        for entry in breakdown:
            if entry.shortfall > 0:
                satisfiable = False
                messages.append(
                    f"{QUESTION_TYPE_LABELS[entry.type]}: {entry.requested} requested but only "
                    f"{entry.available} available in the question bank ({entry.shortfall} short)."
                )
        total_shortfall = sum(entry.shortfall for entry in breakdown)
    else:
        total_shortfall = max(0, config.question_count - available_total)
        if available_total < config.question_count:
            satisfiable = False
            messages.append(
                f"The quiz requires {config.question_count} questions but only {available_total} "
                f"are available across the selected question types ({total_shortfall} short)."
            )

    return CapacityReport(
        satisfiable=satisfiable,
        requested_total=config.question_count,
        available_total=available_total,
        total_shortfall=total_shortfall,
        breakdown=tuple(breakdown),
        messages=tuple(messages),
    )


def capacity_to_json(report: CapacityReport) -> dict[str, Any]:
    return {
        "satisfiable": report.satisfiable,
        "requestedTotal": report.requested_total,
        "availableTotal": report.available_total,
        "totalShortfall": report.total_shortfall,
        "breakdown": [
            {
                "type": entry.type.value,
                "requested": entry.requested,
                "available": entry.available,
                "shortfall": entry.shortfall,
            }
            for entry in report.breakdown
        ],
        "messages": list(report.messages),
    }


# ---------------------------------------------------------------------------
# Version fingerprinting
# ---------------------------------------------------------------------------


def fingerprint_configuration(config: QuizConfiguration) -> str:
    """Stable hash of the settings that define a version.

    Distinguishes a *meaningful* change — which creates a new immutable version — from a no-op
    re-save of the active configuration. Order-insensitive by construction: question types are
    canonically sorted and topic ids are sorted, so reordering the form cannot fake a change.
    """
    canonical = {
        "questionCount": config.question_count,
        "timeLimitMinutes": config.time_limit_minutes,
        "passMark": config.pass_mark,
        "randomiseQuestions": config.randomise_questions,
        "maxAttempts": config.max_attempts,
        "deliveryMode": config.delivery_mode.value,
        "questionTypes": [
            [entry.type.value, entry.quota] for entry in sort_question_types(config.question_types)
        ],
        "topicIds": sorted(config.topic_ids),
        "questionPresentation": config.question_presentation.value,
        "randomiseOptionOrder": config.randomise_option_order,
        "allowIncompleteSubmission": config.allow_incomplete_submission,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
