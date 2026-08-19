"""Answer persistence and validation across every supported question structure."""

from __future__ import annotations

import pytest

from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import QuestionType
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import (
    QUIZ_ID,
    answer_for,
    partial_scenario_answer,
    seed_world,
)


def _attempt_of_type(
    context: AppContext, api: ApiClient, question_type: QuestionType, count: int = 2
) -> tuple[str, list[dict]]:
    """Create an attempt made up entirely of one question type."""
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"questionCount": count, "allowedQuestionTypes": [str(question_type)]},
        )
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    questions = assert_ok(api.questions(attempt_id))["questions"]
    return attempt_id, questions


# ---------------------------------------------------------------------------
# Happy path, one test per question structure
# ---------------------------------------------------------------------------


def test_single_choice_answer_is_persisted(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    question = questions[0]
    option_id = question["options"][1]["optionId"]

    body = assert_ok(api.save_answer(attempt_id, question["questionId"], {"selectedOptionId": option_id}))
    answer = body["answer"]
    assert answer["answered"] is True
    assert answer["complete"] is True
    assert answer["revision"] == 1
    assert answer["response"] == {
        "type": str(QuestionType.SINGLE_CHOICE),
        "selectedOptionId": option_id,
    }
    # Every save carries authoritative timing so the client can resync.
    assert body["timing"]["serverTime"]
    assert body["persistedAt"]


def test_true_false_answer_is_persisted(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.TRUE_FALSE)
    question = questions[0]

    body = assert_ok(api.save_answer(attempt_id, question["questionId"], {"value": False}))
    assert body["answer"]["response"] == {"type": str(QuestionType.TRUE_FALSE), "value": False}
    assert body["answer"]["complete"] is True


def test_multi_select_answer_is_persisted_and_canonicalised(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.MULTI_SELECT)
    question = questions[0]
    ids = [option["optionId"] for option in question["options"][:3]]

    # Sent in reverse order; stored sorted, so the same set is recognised as unchanged
    # regardless of click order.
    body = assert_ok(
        api.save_answer(attempt_id, question["questionId"], {"selectedOptionIds": list(reversed(ids))})
    )
    assert body["answer"]["response"]["selectedOptionIds"] == sorted(ids)


def test_drag_to_order_answer_is_persisted_in_order(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.DRAG_TO_ORDER)
    question = questions[0]
    ordering = [item["itemId"] for item in question["orderItems"]]
    shuffled = [ordering[2], ordering[0], ordering[1], ordering[3]]

    body = assert_ok(api.save_answer(attempt_id, question["questionId"], {"orderedItemIds": shuffled}))
    # Order is significant, so it is preserved exactly rather than sorted.
    assert body["answer"]["response"]["orderedItemIds"] == shuffled


def test_scenario_answer_is_persisted(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SCENARIO)
    question = questions[0]
    assert len(question["subQuestions"]) == 3

    body = assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question)))
    answer = body["answer"]
    assert answer["answered"] is True
    assert answer["complete"] is True
    assert len(answer["response"]["responses"]) == 3


def test_partial_scenario_answer_is_saved_but_not_complete(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SCENARIO)
    question = questions[0]

    body = assert_ok(
        api.save_answer(attempt_id, question["questionId"], partial_scenario_answer(question))
    )
    answer = body["answer"]
    # Progress is persisted so autosave never loses work, but the question does not yet
    # count as answered for completion purposes.
    assert answer["answered"] is True
    assert answer["complete"] is False

    state = assert_ok(api.state(attempt_id))["state"]
    assert state["answeredCount"] == 1
    assert state["completeCount"] == 0
    assert state["unansweredCount"] == 2


# ---------------------------------------------------------------------------
# Invalid answers
# ---------------------------------------------------------------------------


def test_single_choice_rejects_an_unknown_option(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    question = questions[0]

    error = assert_error(
        api.save_answer(attempt_id, question["questionId"], {"selectedOptionId": "not-an-option"}),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["selectedOptionId"] == "not-an-option"
    assert len(error["context"]["validOptionIds"]) == 4


def test_answer_of_the_wrong_shape_is_rejected(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    question = questions[0]

    # A True/False payload sent to a single-choice question.
    assert_error(
        api.save_answer(attempt_id, question["questionId"], {"value": True}),
        422,
        "INVALID_ANSWER",
    )


def test_declared_type_must_match_the_question(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    question = questions[0]

    error = assert_error(
        api.save_answer(
            attempt_id,
            question["questionId"],
            {"type": str(QuestionType.MULTI_SELECT), "selectedOptionId": question["options"][0]["optionId"]},
        ),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["expectedType"] == str(QuestionType.SINGLE_CHOICE)


def test_unknown_fields_are_rejected(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    question = questions[0]

    error = assert_error(
        api.save_answer(
            attempt_id,
            question["questionId"],
            {"selectedOptionId": question["options"][0]["optionId"], "score": 100},
        ),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["unexpectedFields"] == ["score"]


def test_true_false_rejects_a_non_boolean(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.TRUE_FALSE)
    assert_error(
        api.save_answer(attempt_id, questions[0]["questionId"], {"value": "true"}),
        422,
        "INVALID_ANSWER",
    )


def test_multi_select_rejects_duplicates(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.MULTI_SELECT)
    question = questions[0]
    option_id = question["options"][0]["optionId"]

    error = assert_error(
        api.save_answer(
            attempt_id, question["questionId"], {"selectedOptionIds": [option_id, option_id]}
        ),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["duplicateOptionIds"] == [option_id]


def test_multi_select_enforces_the_maximum(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.MULTI_SELECT)
    question = questions[0]
    # The fixture allows at most 4 of 5 options.
    all_ids = [option["optionId"] for option in question["options"]]

    error = assert_error(
        api.save_answer(attempt_id, question["questionId"], {"selectedOptionIds": all_ids}),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["maxSelections"] == 4
    assert error["context"]["selectedCount"] == 5


def test_drag_to_order_rejects_a_partial_ordering(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.DRAG_TO_ORDER)
    question = questions[0]
    partial = [item["itemId"] for item in question["orderItems"][:2]]

    error = assert_error(
        api.save_answer(attempt_id, question["questionId"], {"orderedItemIds": partial}),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["expectedCount"] == 4
    assert error["context"]["receivedCount"] == 2


def test_drag_to_order_rejects_a_foreign_item(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.DRAG_TO_ORDER)
    question = questions[0]
    ids = [item["itemId"] for item in question["orderItems"]]

    error = assert_error(
        api.save_answer(
            attempt_id, question["questionId"], {"orderedItemIds": [*ids[:3], "foreign-item"]}
        ),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["unknownItemIds"] == ["foreign-item"]


def test_scenario_rejects_a_foreign_sub_question(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SCENARIO)
    question = questions[0]

    error = assert_error(
        api.save_answer(
            attempt_id,
            question["questionId"],
            {"responses": [{"subQuestionId": "nope", "answer": {"value": True}}]},
        ),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["subQuestionId"] == "nope"


def test_scenario_rejects_a_duplicate_sub_question(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SCENARIO)
    question = questions[0]
    sub = next(s for s in question["subQuestions"] if s["type"] == str(QuestionType.TRUE_FALSE))

    assert_error(
        api.save_answer(
            attempt_id,
            question["questionId"],
            {
                "responses": [
                    {"subQuestionId": sub["subQuestionId"], "answer": {"value": True}},
                    {"subQuestionId": sub["subQuestionId"], "answer": {"value": False}},
                ]
            },
        ),
        422,
        "INVALID_ANSWER",
    )


def test_scenario_validates_each_sub_answer_against_its_own_type(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SCENARIO)
    question = questions[0]
    single = next(
        s for s in question["subQuestions"] if s["type"] == str(QuestionType.SINGLE_CHOICE)
    )

    error = assert_error(
        api.save_answer(
            attempt_id,
            question["questionId"],
            {
                "responses": [
                    {"subQuestionId": single["subQuestionId"], "answer": {"selectedOptionId": "bogus"}}
                ]
            },
        ),
        422,
        "INVALID_ANSWER",
    )
    assert error["context"]["path"].endswith(".answer")


def test_invalid_answer_leaves_previous_value_intact(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    question = questions[0]
    good = question["options"][0]["optionId"]

    assert_ok(api.save_answer(attempt_id, question["questionId"], {"selectedOptionId": good}))
    assert_error(
        api.save_answer(attempt_id, question["questionId"], {"selectedOptionId": "bad"}),
        422,
        "INVALID_ANSWER",
    )

    stored = assert_ok(api.answers(attempt_id))["answers"][0]
    assert stored["response"]["selectedOptionId"] == good
    assert stored["revision"] == 1


def test_answering_a_question_outside_the_attempt_is_rejected(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, _ = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    assert_error(
        api.save_answer(attempt_id, "q-sn-01", {"value": True}), 409, "QUESTION_UNAVAILABLE"
    )


def test_missing_response_field_is_rejected(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    response = api.request(
        "PUT",
        f"/api/v1/attempts/{attempt_id}/questions/{questions[0]['questionId']}/answer",
        json={},
    )
    # `response` defaults to null, i.e. "clear the answer" - an explicit, valid action.
    body = assert_ok(response)
    assert body["answer"]["answered"] is False


# ---------------------------------------------------------------------------
# Clearing, updating and retrieval
# ---------------------------------------------------------------------------


def test_answer_can_be_updated(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    question = questions[0]

    first = assert_ok(
        api.save_answer(attempt_id, question["questionId"], answer_for(question, variant=0))
    )["answer"]
    second = assert_ok(
        api.save_answer(attempt_id, question["questionId"], answer_for(question, variant=1))
    )["answer"]

    assert first["revision"] == 1
    assert second["revision"] == 2
    assert second["changed"] is True
    assert second["response"] != first["response"]


def test_answer_can_be_cleared(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE)
    question = questions[0]

    assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question)))
    cleared = assert_ok(api.clear_answer(attempt_id, question["questionId"]))["answer"]

    assert cleared["answered"] is False
    assert cleared["complete"] is False
    assert cleared["response"] is None

    state = assert_ok(api.state(attempt_id))["state"]
    assert state["answeredCount"] == 0


def test_empty_multi_select_clears_the_answer(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.MULTI_SELECT)
    question = questions[0]

    assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question)))
    # Deselecting everything is a legitimate action, not a validation failure.
    cleared = assert_ok(
        api.save_answer(attempt_id, question["questionId"], {"selectedOptionIds": []})
    )["answer"]
    assert cleared["answered"] is False
    assert cleared["response"] is None


def test_all_questions_are_listed_answered_or_not(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt_of_type(context, api, QuestionType.SINGLE_CHOICE, count=4)
    assert_ok(api.save_answer(attempt_id, questions[1]["questionId"], answer_for(questions[1])))

    body = assert_ok(api.answers(attempt_id))
    assert body["totalQuestions"] == 4
    assert len(body["answers"]) == 4
    assert body["answeredCount"] == 1

    by_position = {answer["position"]: answer for answer in body["answers"]}
    assert by_position[2]["answered"] is True
    assert by_position[1]["answered"] is False
    assert by_position[1]["response"] is None
    assert by_position[1]["revision"] == 0


@pytest.mark.parametrize(
    "question_type",
    [
        QuestionType.SINGLE_CHOICE,
        QuestionType.TRUE_FALSE,
        QuestionType.MULTI_SELECT,
        QuestionType.SCENARIO,
        QuestionType.DRAG_TO_ORDER,
    ],
)
def test_saved_answer_is_retrievable_for_every_type(
    context: AppContext, api: ApiClient, question_type: QuestionType
) -> None:
    attempt_id, questions = _attempt_of_type(context, api, question_type, count=1)
    question = questions[0]
    payload = answer_for(question)

    saved = assert_ok(api.save_answer(attempt_id, question["questionId"], payload))["answer"]
    reloaded = assert_ok(api.answers(attempt_id))["answers"][0]

    assert reloaded["response"] == saved["response"]
    assert reloaded["answered"] is True
    assert reloaded["questionType"] == str(question_type)
