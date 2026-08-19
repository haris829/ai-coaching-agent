"""Describing the learner's own answer back to them (§11).

UC-03's answer payload is a mapping whose shape depends on the question type. This module turns it
into the neutral description the coach is given.

"Neutral" is the whole job. Every temptation to be helpful here is a leak:

* labelling which selections were right — half the answer key for a multi-select;
* saying "you missed one" — the size of the key;
* ordering the selections by anything other than the delivered order — a hint;
* falling back to the answer key when the response is unreadable — the key itself.

So this module reads only the learner's submission and the *delivered* option text, and when it
cannot make sense of a response it says so plainly rather than reaching for a better source.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.modules.coaching.domain.context import LearnerResponseView
from app.modules.coaching.integration.uc03 import DeliveredQuestion, LearnerAnswer

#: Response keys UC-03 uses. Kept in one place so an added question type is a one-line change.
_SELECTED_ONE = ("selected_option_id", "option_id")
_SELECTED_MANY = ("selected_option_ids", "option_ids")
_ORDERED = ("ordered_item_ids", "item_ids")
_BOOLEAN = ("value", "boolean_value")
_FREE_TEXT = ("text", "free_text", "answer_text")


def _string_list(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, Sequence):
        return tuple(str(item) for item in raw if isinstance(item, str | int | float))
    return ()


def _first(response: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in response and response[key] is not None:
            return response[key]
    return None


def _labels(question: DeliveredQuestion, option_ids: Sequence[str]) -> tuple[str, ...]:
    """Option text for the ids the learner picked, in the order the learner picked them.

    An id with no delivered option resolves to nothing rather than to a placeholder: the coach
    should not be told about an option that was never on screen.
    """
    resolved: list[str] = []
    for option_id in option_ids:
        option = question.option(option_id)
        if option is not None and option.text:
            resolved.append(option.text)
    return tuple(resolved)


def _item_labels(question: DeliveredQuestion, item_ids: Sequence[str]) -> tuple[str, ...]:
    resolved: list[str] = []
    for item_id in item_ids:
        item = question.order_item(item_id)
        if item is not None and item.text:
            resolved.append(item.text)
    return tuple(resolved)


def describe_learner_answer(
    question: DeliveredQuestion, answer: LearnerAnswer | None
) -> LearnerResponseView:
    """Render one learner answer as the neutral view the coach receives.

    Returns an ``answered=False`` view when nothing was submitted. That case is reachable in
    practice — a deployment whose UC-04 reports unanswered questions as INCORRECT will coach them
    — and the coach is simply told the question was left blank, which is true and is enough to open
    a conversation with.
    """
    if answer is None or not answer.has_response or answer.response is None:
        return LearnerResponseView(answered=False, summary="No answer was submitted.")

    response: Mapping[str, Any] = answer.response

    selected_ids = _string_list(_first(response, _SELECTED_MANY))
    if not selected_ids:
        single = _first(response, _SELECTED_ONE)
        selected_ids = _string_list(single) if single is not None else ()

    ordered_ids = _string_list(_first(response, _ORDERED))

    raw_boolean = _first(response, _BOOLEAN)
    boolean_value = raw_boolean if isinstance(raw_boolean, bool) else None

    raw_text = _first(response, _FREE_TEXT)
    free_text = raw_text.strip() if isinstance(raw_text, str) and raw_text.strip() else None

    selected_labels = _labels(question, selected_ids)
    ordered_labels = _item_labels(question, ordered_ids)

    return LearnerResponseView(
        answered=True,
        summary=_summarise(
            selected_labels=selected_labels,
            selected_ids=selected_ids,
            ordered_labels=ordered_labels,
            ordered_ids=ordered_ids,
            boolean_value=boolean_value,
            free_text=free_text,
        ),
        selected_option_ids=selected_ids,
        selected_option_labels=selected_labels,
        ordered_item_ids=ordered_ids,
        ordered_item_labels=ordered_labels,
        boolean_value=boolean_value,
        free_text=free_text,
    )


def _summarise(
    *,
    selected_labels: tuple[str, ...],
    selected_ids: tuple[str, ...],
    ordered_labels: tuple[str, ...],
    ordered_ids: tuple[str, ...],
    boolean_value: bool | None,
    free_text: str | None,
) -> str:
    """One sentence describing the submission, with no evaluation of it."""
    if selected_labels:
        return "The learner selected: " + "; ".join(selected_labels)
    if selected_ids:
        return "The learner selected option(s): " + ", ".join(selected_ids)
    if ordered_labels:
        return "The learner ordered the steps as: " + " → ".join(ordered_labels)
    if ordered_ids:
        return "The learner ordered the items as: " + " → ".join(ordered_ids)
    if boolean_value is not None:
        return f"The learner answered {'True' if boolean_value else 'False'}."
    if free_text:
        return f"The learner wrote: {free_text}"
    # A response was recorded but its shape is not one this module recognises. Saying so is
    # better than guessing, and far better than consulting the answer key to fill the gap.
    return "The learner submitted an answer that could not be described in detail."
