"""Question flagging and navigation state."""

from __future__ import annotations

from app.core.time import FixedClock, parse_instant
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import QuestionType
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import QUIZ_ID, answer_for, partial_scenario_answer, seed_world


def _attempt(context: AppContext, api: ApiClient, count: int = 5) -> tuple[str, list[dict]]:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": count})
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    return attempt_id, assert_ok(api.questions(attempt_id))["questions"]


# ---------------------------------------------------------------------------
# Flagging
# ---------------------------------------------------------------------------


def test_flag_and_unflag(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    question_id = questions[1]["questionId"]

    flagged = assert_ok(api.set_flag(attempt_id, question_id, True))["flag"]
    assert flagged["flagged"] is True
    assert flagged["flaggedAt"] is not None
    assert flagged["position"] == 2

    unflagged = assert_ok(api.set_flag(attempt_id, question_id, False))["flag"]
    assert unflagged["flagged"] is False
    assert unflagged["flaggedAt"] is None


def test_delete_unflags(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    question_id = questions[0]["questionId"]

    assert_ok(api.set_flag(attempt_id, question_id, True))
    assert assert_ok(api.unflag(attempt_id, question_id))["flag"]["flagged"] is False
    assert assert_ok(api.flags(attempt_id))["flaggedCount"] == 0


def test_flagging_is_idempotent_and_preserves_the_original_instant(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api)
    question_id = questions[0]["questionId"]

    first = assert_ok(api.set_flag(attempt_id, question_id, True))["flag"]

    clock.advance(seconds=120)
    again = assert_ok(api.set_flag(attempt_id, question_id, True))["flag"]

    # Repeating the request is accepted rather than an error...
    assert again["flagged"] is True
    # ...and "when did the learner mark this" stays meaningful.
    assert again["flaggedAt"] == first["flaggedAt"]
    assert parse_instant(again["updatedAt"]) > parse_instant(first["updatedAt"])


def test_re_flagging_after_unflagging_sets_a_new_instant(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api)
    question_id = questions[0]["questionId"]

    first = assert_ok(api.set_flag(attempt_id, question_id, True))["flag"]
    assert_ok(api.set_flag(attempt_id, question_id, False))
    clock.advance(seconds=60)
    second = assert_ok(api.set_flag(attempt_id, question_id, True))["flag"]

    assert second["flaggedAt"] != first["flaggedAt"]


def test_flag_state_survives_reconnection(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    assert_ok(api.set_flag(attempt_id, questions[0]["questionId"], True))
    assert_ok(api.set_flag(attempt_id, questions[3]["questionId"], True))

    body = assert_ok(api.flags(attempt_id))
    assert body["flaggedCount"] == 2
    # Every question is listed, so a client rebuilds its whole view from one response.
    assert len(body["flags"]) == 5
    assert [flag["position"] for flag in body["flags"] if flag["flagged"]] == [1, 4]


def test_flagging_a_question_outside_the_attempt_is_rejected(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, _ = _attempt(context, api)
    error = assert_error(
        api.set_flag(attempt_id, "q-not-delivered", True), 409, "INVALID_FLAG_OPERATION"
    )
    assert error["context"]["questionId"] == "q-not-delivered"


def test_flagging_is_rejected_after_submission(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    assert_ok(api.submit(attempt_id, idempotency_key="key-1"))

    assert_error(
        api.set_flag(attempt_id, questions[0]["questionId"], True),
        409,
        "ATTEMPT_ALREADY_SUBMITTED",
    )


def test_flag_state_is_preserved_through_submission(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    assert_ok(api.set_flag(attempt_id, questions[2]["questionId"], True))
    assert_ok(api.submit(attempt_id, idempotency_key="key-1"))

    # The record of what the learner flagged is retained after submission.
    assert assert_ok(api.flags(attempt_id))["flaggedCount"] == 1


def test_flags_require_a_boolean(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    response = api.request(
        "PUT",
        f"/api/v1/attempts/{attempt_id}/questions/{questions[0]['questionId']}/flag",
        json={"flagged": "yes"},
    )
    assert_error(response, 400, "BAD_REQUEST")


# ---------------------------------------------------------------------------
# Navigation state
# ---------------------------------------------------------------------------


def test_navigation_state_reports_answered_unanswered_and_flagged(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)

    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    assert_ok(api.save_answer(attempt_id, questions[1]["questionId"], answer_for(questions[1])))
    assert_ok(api.set_flag(attempt_id, questions[1]["questionId"], True))
    assert_ok(api.set_flag(attempt_id, questions[4]["questionId"], True))

    state = assert_ok(api.state(attempt_id))["state"]

    assert state["totalQuestions"] == 5
    assert state["answeredCount"] == 2
    assert state["completeCount"] == 2
    assert state["unansweredCount"] == 3
    assert state["flaggedCount"] == 2

    by_position = {entry["position"]: entry for entry in state["questions"]}
    assert by_position[1] == {
        "questionId": questions[0]["questionId"],
        "position": 1,
        "questionType": questions[0]["questionType"],
        "answered": True,
        "complete": True,
        "flagged": False,
    }
    assert by_position[2]["answered"] is True and by_position[2]["flagged"] is True
    assert by_position[3]["answered"] is False and by_position[3]["flagged"] is False
    assert by_position[5]["answered"] is False and by_position[5]["flagged"] is True


def test_navigation_state_includes_timing_and_cursor(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, _ = _attempt(context, api)
    assert_ok(api.set_cursor(attempt_id, 4))
    clock.advance(seconds=45)

    state = assert_ok(api.state(attempt_id))["state"]
    assert state["currentPosition"] == 4
    assert state["timing"]["elapsedSeconds"] == 45
    assert state["timing"]["remainingSeconds"] == 1800 - 45


def test_navigation_state_distinguishes_partially_answered_scenarios(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"questionCount": 2, "allowedQuestionTypes": [str(QuestionType.SCENARIO)]},
        )
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    questions = assert_ok(api.questions(attempt_id))["questions"]

    assert_ok(
        api.save_answer(attempt_id, questions[0]["questionId"], partial_scenario_answer(questions[0]))
    )
    assert_ok(api.save_answer(attempt_id, questions[1]["questionId"], answer_for(questions[1])))

    state = assert_ok(api.state(attempt_id))["state"]
    # Both have something stored, but only one is fully answered.
    assert state["answeredCount"] == 2
    assert state["completeCount"] == 1
    assert state["unansweredCount"] == 1

    by_position = {entry["position"]: entry for entry in state["questions"]}
    assert by_position[1]["answered"] is True and by_position[1]["complete"] is False
    assert by_position[2]["answered"] is True and by_position[2]["complete"] is True


def test_all_at_once_questions_carry_their_state_inline(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    assert_ok(api.set_flag(attempt_id, questions[1]["questionId"], True))

    delivered = assert_ok(api.questions(attempt_id))["questions"]
    assert delivered[0]["answered"] is True
    assert delivered[0]["flagged"] is False
    assert delivered[1]["answered"] is False
    assert delivered[1]["flagged"] is True
