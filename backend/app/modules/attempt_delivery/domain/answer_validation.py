"""Answer validation.

Answer payloads are validated against the *delivered snapshot* of the question, not
against the live question bank. A learner's answer is therefore checked against
exactly what they were shown, and validation keeps working even after UC-02 edits or
retires the question mid-attempt.

Validation is strict by design: unknown keys, unknown option ids, wrong shapes and
duplicate selections are rejected rather than silently coerced. Every failure carries
structured ``details`` so a client can point the learner at the offending field.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.domain.enums import QuestionType
from app.modules.attempt_delivery.integration.uc02.types import (
    BankQuestion,
    QuestionOption,
    QuestionOrderItem,
    ScenarioSubQuestion,
)


@dataclass(frozen=True, slots=True)
class ValidatedAnswer:
    """The outcome of validating one answer payload."""

    #: False when the client cleared the answer.
    answered: bool
    #: True when the response fully answers the question.
    complete: bool
    #: Canonical payload, or ``None`` when cleared.
    canonical: dict[str, Any] | None


CLEARED = ValidatedAnswer(answered=False, complete=False, canonical=None)


# ---------------------------------------------------------------------------
# Canonical serialisation and hashing
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Deterministic JSON with object keys sorted.

    Two logically identical answers must serialise identically, otherwise a repeated
    autosave would look like a change and bump the revision. Array order is preserved
    because it is significant for ``DRAG_TO_ORDER``; set-like collections are sorted
    during validation instead.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_answer(canonical: dict[str, Any] | None) -> str | None:
    """SHA-256 of the canonical payload, used to detect a no-op save."""
    if canonical is None:
        return None
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, message: str, **details: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise errors.invalid_answer(message, **details)
    return value


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise errors.invalid_answer(
            f"Unexpected field(s) in answer payload: {', '.join(unknown)}.",
            path=path,
            unexpectedFields=unknown,
            allowedFields=sorted(allowed),
        )


def _require_id_list(value: Any, field: str, path: str) -> list[str]:
    if not isinstance(value, list):
        raise errors.invalid_answer(
            f'"{field}" must be an array of identifiers.', path=path, field=field
        )
    for item in value:
        # `bool` is a subclass of `int`, but neither is an identifier here.
        if not isinstance(item, str) or item == "":
            raise errors.invalid_answer(
                f'"{field}" must contain only non-empty string identifiers.',
                path=path,
                field=field,
            )
    return list(value)


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicated: set[str] = set()
    for value in values:
        if value in seen:
            duplicated.add(value)
        seen.add(value)
    return sorted(duplicated)


@dataclass(frozen=True, slots=True)
class _PrimitiveShape:
    """The answerable structure of a primitive question or scenario sub-question."""

    type: QuestionType
    question_id: str
    options: tuple[QuestionOption, ...] = ()
    order_items: tuple[QuestionOrderItem, ...] = ()
    min_selections: int | None = None
    max_selections: int | None = None

    @property
    def option_ids(self) -> list[str]:
        return [option.option_id for option in self.options]

    @property
    def item_ids(self) -> list[str]:
        return [item.item_id for item in self.order_items]


def _shape_of_question(question: BankQuestion) -> _PrimitiveShape:
    return _PrimitiveShape(
        type=question.type,
        question_id=question.question_id,
        options=question.options,
        order_items=question.order_items,
        min_selections=question.min_selections,
        max_selections=question.max_selections,
    )


def _shape_of_sub_question(sub: ScenarioSubQuestion, question_id: str) -> _PrimitiveShape:
    return _PrimitiveShape(
        type=sub.type,
        question_id=question_id,
        options=sub.options,
        order_items=sub.order_items,
        min_selections=sub.min_selections,
        max_selections=sub.max_selections,
    )


# ---------------------------------------------------------------------------
# Primitive validators
# ---------------------------------------------------------------------------


def _validate_primitive(shape: _PrimitiveShape, raw: Any, path: str) -> dict[str, Any] | None:
    """Validate one primitive answer.

    Returns ``None`` when the payload represents an intentionally empty answer
    (an empty ``MULTI_SELECT`` or ``DRAG_TO_ORDER`` list), which the caller records as
    "unanswered" rather than rejecting.
    """
    payload = _require_mapping(
        raw, "The answer payload must be an object.", path=path, expectedType=str(shape.type)
    )

    # An explicit `type` is optional but, when present, must agree with the delivered
    # question. This catches a client answering the wrong question.
    declared = payload.get("type")
    if declared is not None and declared != str(shape.type):
        raise errors.invalid_answer(
            f'Answer type "{declared}" does not match the question type "{shape.type}".',
            path=path,
            expectedType=str(shape.type),
            receivedType=declared,
        )

    if shape.type is QuestionType.SINGLE_CHOICE:
        return _validate_single_choice(shape, payload, path)
    if shape.type is QuestionType.TRUE_FALSE:
        return _validate_true_false(shape, payload, path)
    if shape.type is QuestionType.MULTI_SELECT:
        return _validate_multi_select(shape, payload, path)
    if shape.type is QuestionType.DRAG_TO_ORDER:
        return _validate_drag_to_order(shape, payload, path)

    raise errors.invalid_answer(f'Unsupported question type "{shape.type}".', path=path)


def _validate_single_choice(
    shape: _PrimitiveShape, payload: dict[str, Any], path: str
) -> dict[str, Any]:
    _reject_unknown_keys(payload, {"type", "selectedOptionId"}, path)
    if not shape.options:
        raise errors.question_unavailable(shape.question_id)

    selected = payload.get("selectedOptionId")
    if not isinstance(selected, str) or selected == "":
        raise errors.invalid_answer(
            '"selectedOptionId" must be a non-empty string.',
            path=path,
            expectedType=str(shape.type),
        )
    if selected not in shape.option_ids:
        raise errors.invalid_answer(
            '"selectedOptionId" is not one of the options presented for this question.',
            path=path,
            selectedOptionId=selected,
            validOptionIds=shape.option_ids,
        )
    return {"type": str(QuestionType.SINGLE_CHOICE), "selectedOptionId": selected}


def _validate_true_false(
    shape: _PrimitiveShape, payload: dict[str, Any], path: str
) -> dict[str, Any]:
    _reject_unknown_keys(payload, {"type", "value"}, path)
    value = payload.get("value")
    if not isinstance(value, bool):
        raise errors.invalid_answer(
            '"value" must be a boolean for a True/False question.',
            path=path,
            expectedType=str(shape.type),
        )
    return {"type": str(QuestionType.TRUE_FALSE), "value": value}


def _validate_multi_select(
    shape: _PrimitiveShape, payload: dict[str, Any], path: str
) -> dict[str, Any] | None:
    _reject_unknown_keys(payload, {"type", "selectedOptionIds"}, path)
    if not shape.options:
        raise errors.question_unavailable(shape.question_id)

    selected = _require_id_list(payload.get("selectedOptionIds"), "selectedOptionIds", path)

    # Deselecting everything is a legitimate action; it clears the answer.
    if not selected:
        return None

    duplicated = _duplicates(selected)
    if duplicated:
        raise errors.invalid_answer(
            '"selectedOptionIds" must not contain duplicates.',
            path=path,
            duplicateOptionIds=duplicated,
        )
    unknown = [item for item in selected if item not in shape.option_ids]
    if unknown:
        raise errors.invalid_answer(
            '"selectedOptionIds" contains options not presented for this question.',
            path=path,
            unknownOptionIds=unknown,
            validOptionIds=shape.option_ids,
        )
    if shape.min_selections is not None and len(selected) < shape.min_selections:
        raise errors.invalid_answer(
            f"At least {shape.min_selections} option(s) must be selected.",
            path=path,
            minSelections=shape.min_selections,
            selectedCount=len(selected),
        )
    if shape.max_selections is not None and len(selected) > shape.max_selections:
        raise errors.invalid_answer(
            f"At most {shape.max_selections} option(s) may be selected.",
            path=path,
            maxSelections=shape.max_selections,
            selectedCount=len(selected),
        )

    # Sorted so the same set saved in a different click order is recognised as
    # unchanged, which keeps repeated autosaves idempotent.
    return {"type": str(QuestionType.MULTI_SELECT), "selectedOptionIds": sorted(selected)}


def _validate_drag_to_order(
    shape: _PrimitiveShape, payload: dict[str, Any], path: str
) -> dict[str, Any] | None:
    _reject_unknown_keys(payload, {"type", "orderedItemIds"}, path)
    valid_items = shape.item_ids
    if not valid_items:
        raise errors.question_unavailable(shape.question_id)

    ordered = _require_id_list(payload.get("orderedItemIds"), "orderedItemIds", path)
    if not ordered:
        return None

    duplicated = _duplicates(ordered)
    if duplicated:
        raise errors.invalid_answer(
            '"orderedItemIds" must not contain duplicates.', path=path, duplicateItemIds=duplicated
        )
    unknown = [item for item in ordered if item not in valid_items]
    if unknown:
        raise errors.invalid_answer(
            '"orderedItemIds" contains items not presented for this question.',
            path=path,
            unknownItemIds=unknown,
            validItemIds=valid_items,
        )
    # A drag-to-order answer is only meaningful as a complete ordering.
    if len(ordered) != len(valid_items):
        raise errors.invalid_answer(
            '"orderedItemIds" must contain every item for this question exactly once. '
            "Clear the answer instead of saving a partial ordering.",
            path=path,
            expectedCount=len(valid_items),
            receivedCount=len(ordered),
            validItemIds=valid_items,
        )
    return {"type": str(QuestionType.DRAG_TO_ORDER), "orderedItemIds": ordered}


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


def _validate_scenario(question: BankQuestion, raw: Any) -> ValidatedAnswer:
    payload = _require_mapping(
        raw,
        'A scenario answer must be an object with a "responses" array.',
        questionId=question.question_id,
    )
    _reject_unknown_keys(payload, {"type", "responses"}, "response")

    declared = payload.get("type")
    if declared is not None and declared != str(QuestionType.SCENARIO):
        raise errors.invalid_answer(
            f'Answer type "{declared}" does not match the question type "SCENARIO".',
            questionId=question.question_id,
            expectedType=str(QuestionType.SCENARIO),
            receivedType=declared,
        )

    sub_questions = question.sub_questions
    if not sub_questions:
        raise errors.question_unavailable(question.question_id)

    responses_raw = payload.get("responses")
    if not isinstance(responses_raw, list):
        raise errors.invalid_answer(
            '"responses" must be an array.', questionId=question.question_id
        )
    if not responses_raw:
        return CLEARED

    by_id = {sub.sub_question_id: sub for sub in sub_questions}
    seen: set[str] = set()
    responses: list[dict[str, Any]] = []

    for index, entry in enumerate(responses_raw):
        path = f"response.responses[{index}]"
        item = _require_mapping(entry, "Each scenario response must be an object.", path=path)
        _reject_unknown_keys(item, {"subQuestionId", "answer"}, path)

        sub_question_id = item.get("subQuestionId")
        if not isinstance(sub_question_id, str) or sub_question_id == "":
            raise errors.invalid_answer('"subQuestionId" must be a non-empty string.', path=path)
        sub = by_id.get(sub_question_id)
        if sub is None:
            raise errors.invalid_answer(
                '"subQuestionId" does not belong to this scenario question.',
                path=path,
                subQuestionId=sub_question_id,
                validSubQuestionIds=sorted(by_id),
            )
        if sub_question_id in seen:
            raise errors.invalid_answer(
                "Duplicate response for the same sub-question.",
                path=path,
                subQuestionId=sub_question_id,
            )
        seen.add(sub_question_id)

        answer = _validate_primitive(
            _shape_of_sub_question(sub, question.question_id), item.get("answer"), f"{path}.answer"
        )
        # A sub-answer that validates as empty is omitted, leaving the scenario
        # partially answered rather than failing the whole save.
        if answer is not None:
            responses.append({"subQuestionId": sub_question_id, "answer": answer})

    if not responses:
        return CLEARED

    # Sorted for a stable canonical form regardless of the order the client sent.
    responses.sort(key=lambda item: item["subQuestionId"])

    return ValidatedAnswer(
        answered=True,
        complete=len(responses) == len(sub_questions),
        canonical={"type": str(QuestionType.SCENARIO), "responses": responses},
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_answer(question: BankQuestion, raw: Any) -> ValidatedAnswer:
    """Validate a raw answer payload against a delivered question snapshot.

    Pass ``None`` to clear the answer. Anything invalid raises
    :func:`app.domain.errors.invalid_answer` with machine-readable details.
    """
    if raw is None:
        return CLEARED

    if question.type is QuestionType.SCENARIO:
        return _validate_scenario(question, raw)

    canonical = _validate_primitive(_shape_of_question(question), raw, "response")
    if canonical is None:
        return CLEARED
    return ValidatedAnswer(answered=True, complete=True, canonical=canonical)
