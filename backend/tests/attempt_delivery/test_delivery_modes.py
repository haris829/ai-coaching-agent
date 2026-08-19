"""Delivery modes: one-at-a-time and all-at-once.

The mode is read from the attempt's locked configuration and enforced server-side, so
it is a real constraint rather than a frontend convention.
"""

from __future__ import annotations

from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import QuestionPresentation, QuestionType
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import QUIZ_ID, seed_world


def _one_at_a_time(context: AppContext, **rules: object) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionPresentation": str(QuestionPresentation.ONE_AT_A_TIME),
                "questionCount": 4,
                "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)],
                **rules,
            },
        )


def _all_at_once(context: AppContext, **rules: object) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionPresentation": str(QuestionPresentation.ALL_AT_ONCE),
                "questionCount": 4,
                "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)],
                **rules,
            },
        )


# ---------------------------------------------------------------------------
# All-at-once
# ---------------------------------------------------------------------------


def test_all_at_once_returns_the_whole_paper(context: AppContext, api: ApiClient) -> None:
    _all_at_once(context)
    created = assert_ok(api.create_attempt(QUIZ_ID), 201)
    attempt_id = created["attempt"]["attemptId"]

    assert created["delivery"]["questionsUrl"] == f"/api/v1/attempts/{attempt_id}/questions"

    body = assert_ok(api.questions(attempt_id))
    assert body["questionPresentation"] == str(QuestionPresentation.ALL_AT_ONCE)
    assert body["totalQuestions"] == 4
    assert [question["position"] for question in body["questions"]] == [1, 2, 3, 4]
    # Full content, so the client can render the entire paper in one go.
    for question in body["questions"]:
        assert question["prompt"]
        assert len(question["options"]) == 4
        assert question["answered"] is False
        assert question["flagged"] is False


def test_all_at_once_still_supports_single_question_reads(
    context: AppContext, api: ApiClient
) -> None:
    _all_at_once(context)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    body = assert_ok(api.question_at(attempt_id, 2))
    assert body["question"]["position"] == 2
    assert body["navigation"]["previousUrl"].endswith("/questions/at/1")
    assert body["navigation"]["nextUrl"].endswith("/questions/at/3")


# ---------------------------------------------------------------------------
# One-at-a-time
# ---------------------------------------------------------------------------


def test_one_at_a_time_refuses_the_whole_paper(context: AppContext, api: ApiClient) -> None:
    _one_at_a_time(context)
    created = assert_ok(api.create_attempt(QUIZ_ID), 201)
    attempt_id = created["attempt"]["attemptId"]

    # The client is pointed at the per-question endpoint instead.
    assert created["delivery"]["questionsUrl"] == (
        f"/api/v1/attempts/{attempt_id}/questions/current"
    )

    error = assert_error(api.questions(attempt_id), 409, "QUESTION_PRESENTATION_VIOLATION")
    assert error["context"]["questionPresentation"] == str(QuestionPresentation.ONE_AT_A_TIME)
    assert error["context"]["currentPosition"] == 1


def test_one_at_a_time_serves_the_current_question(context: AppContext, api: ApiClient) -> None:
    _one_at_a_time(context)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    body = assert_ok(api.current_question(attempt_id))
    assert body["question"]["position"] == 1
    assert body["navigation"]["isFirst"] is True
    assert body["navigation"]["isLast"] is False
    assert body["navigation"]["previousUrl"] is None
    assert body["timing"]["remainingSeconds"] is not None


def test_cursor_advances_and_survives_a_reload(context: AppContext, api: ApiClient) -> None:
    _one_at_a_time(context)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    assert_ok(api.set_cursor(attempt_id, 3))

    # A reconnecting client resumes on question 3, not question 1.
    body = assert_ok(api.current_question(attempt_id))
    assert body["question"]["position"] == 3
    assert assert_ok(api.get_attempt(attempt_id))["attempt"]["currentPosition"] == 3
    assert assert_ok(api.state(attempt_id))["state"]["currentPosition"] == 3


def test_cursor_rejects_out_of_range_positions(context: AppContext, api: ApiClient) -> None:
    _one_at_a_time(context)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    # 99 is a *domain* check — the schema accepts any position >= 1, the attempt knows it only has
    # four questions. 0 is rejected earlier, by the schema itself.
    assert_error(api.set_cursor(attempt_id, 99), 400, "VALIDATION_ERROR")
    # Pydantic enforces the lower bound before the handler runs.
    assert_error(api.set_cursor(attempt_id, 0), 400, "BAD_REQUEST")
    # The stored cursor is unchanged.
    assert assert_ok(api.get_attempt(attempt_id))["attempt"]["currentPosition"] == 1


def test_navigating_back_is_supported(context: AppContext, api: ApiClient) -> None:
    _one_at_a_time(context)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    last = assert_ok(api.question_at(attempt_id, 4))
    assert last["navigation"]["isLast"] is True
    assert last["navigation"]["nextUrl"] is None

    # Revisiting an earlier question is always allowed.
    first = assert_ok(api.question_at(attempt_id, 1))
    assert first["question"]["position"] == 1


def test_answering_one_at_a_time_works_per_question(context: AppContext, api: ApiClient) -> None:
    _one_at_a_time(context)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    for position in (1, 2, 3, 4):
        body = assert_ok(api.question_at(attempt_id, position))
        question = body["question"]
        assert body["answerState"]["answered"] is False

        assert_ok(
            api.save_answer(
                attempt_id,
                question["questionId"],
                {"selectedOptionId": question["options"][0]["optionId"]},
            )
        )
        assert_ok(api.set_cursor(attempt_id, position))

        # The per-question read carries the learner's state, so no extra round trip.
        after = assert_ok(api.question_at(attempt_id, position))
        assert after["answerState"]["answered"] is True
        assert after["answerState"]["complete"] is True

    state = assert_ok(api.state(attempt_id))["state"]
    assert state["answeredCount"] == 4
    assert state["unansweredCount"] == 0


def test_position_out_of_range_is_a_validation_error(context: AppContext, api: ApiClient) -> None:
    _one_at_a_time(context)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    error = assert_error(api.question_at(attempt_id, 99), 400, "VALIDATION_ERROR")
    assert error["context"]["totalQuestions"] == 4


def test_question_not_in_this_attempt_is_rejected(context: AppContext, api: ApiClient) -> None:
    _one_at_a_time(context)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    # A real bank question that was not selected for this attempt.
    assert_error(api.question(attempt_id, "q-sn-01"), 409, "QUESTION_UNAVAILABLE")
