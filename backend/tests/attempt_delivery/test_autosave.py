"""Autosave support, idempotency, reload state and save-failure reporting.

The backend runs no timer. These tests assert the properties that let a client safely
call the save endpoints every 30 seconds.
"""

from __future__ import annotations

from app.core.time import FixedClock, parse_instant
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import QuestionType
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import QUIZ_ID, answer_for, seed_world


def _attempt(context: AppContext, api: ApiClient, count: int = 5) -> tuple[str, list[dict]]:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": count})
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    return attempt_id, assert_ok(api.questions(attempt_id))["questions"]


# ---------------------------------------------------------------------------
# Idempotency of repeated saves
# ---------------------------------------------------------------------------


def test_repeating_an_identical_save_does_not_advance_the_revision(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api)
    question = questions[0]
    payload = answer_for(question)

    first = assert_ok(api.save_answer(attempt_id, question["questionId"], payload))["answer"]
    assert first["revision"] == 1
    assert first["changed"] is True

    # An autosave loop re-sending the same selection every 30 seconds.
    for tick in range(1, 5):
        clock.advance(seconds=30)
        again = assert_ok(
            api.save_answer(attempt_id, question["questionId"], payload, source="AUTOSAVE")
        )["answer"]
        assert again["revision"] == 1, f"revision advanced on autosave tick {tick}"
        assert again["changed"] is False
        # savedAt still moves, so a client can prove the save landed.
        assert parse_instant(again["savedAt"]) == clock.now()


def test_multi_select_order_does_not_count_as_a_change(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"questionCount": 1, "allowedQuestionTypes": [str(QuestionType.MULTI_SELECT)]},
        )
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    question = assert_ok(api.questions(attempt_id))["questions"][0]
    ids = [option["optionId"] for option in question["options"][:3]]

    first = assert_ok(
        api.save_answer(attempt_id, question["questionId"], {"selectedOptionIds": ids})
    )["answer"]
    # Same set, different click order: canonicalisation recognises it as unchanged.
    second = assert_ok(
        api.save_answer(
            attempt_id, question["questionId"], {"selectedOptionIds": list(reversed(ids))}
        )
    )["answer"]

    assert second["revision"] == first["revision"] == 1
    assert second["changed"] is False


def test_a_genuine_change_advances_the_revision(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    question = questions[0]

    assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question, variant=0)))
    changed = assert_ok(
        api.save_answer(attempt_id, question["questionId"], answer_for(question, variant=1))
    )["answer"]

    assert changed["revision"] == 2
    assert changed["changed"] is True


def test_latest_valid_answer_wins_after_many_saves(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    question = questions[0]

    final = None
    for variant in range(6):
        final = assert_ok(
            api.save_answer(attempt_id, question["questionId"], answer_for(question, variant=variant))
        )["answer"]

    stored = assert_ok(api.answers(attempt_id))["answers"][0]
    assert stored["response"] == final["response"]


# ---------------------------------------------------------------------------
# Batch autosave
# ---------------------------------------------------------------------------


def test_batch_autosave_persists_everything(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)

    payload = [
        {"questionId": question["questionId"], "response": answer_for(question)}
        for question in questions
    ]
    body = assert_ok(api.autosave(attempt_id, payload))

    assert body["savedCount"] == len(questions)
    assert body["changedCount"] == len(questions)
    assert body["persistedAt"]
    # Timing travels with the save, so the client resyncs in the same round trip.
    assert body["timing"]["remainingSeconds"] is not None

    state = assert_ok(api.state(attempt_id))["state"]
    assert state["answeredCount"] == len(questions)


def test_repeated_batch_autosave_is_idempotent(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api)
    payload = [
        {"questionId": question["questionId"], "response": answer_for(question)}
        for question in questions
    ]

    assert_ok(api.autosave(attempt_id, payload))
    for _ in range(3):
        clock.advance(seconds=30)
        body = assert_ok(api.autosave(attempt_id, payload))
        assert body["savedCount"] == len(questions)
        assert body["changedCount"] == 0

    # One revision per question, not one per autosave tick.
    revisions = assert_ok(api.answer_revisions(attempt_id))
    assert revisions["count"] == len(questions)


def test_batch_autosave_is_atomic_on_a_bad_entry(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)

    # First establish a known good state for question 1.
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    baseline = assert_ok(api.answers(attempt_id))["answers"]

    payload = [
        {"questionId": questions[0]["questionId"], "response": answer_for(questions[0], variant=1)},
        {"questionId": questions[1]["questionId"], "response": answer_for(questions[1])},
        # One malformed entry.
        {"questionId": questions[2]["questionId"], "response": {"selectedOptionId": "nope"}},
    ]
    assert_error(api.autosave(attempt_id, payload), 422, "INVALID_ANSWER")

    # Nothing from the batch was written - not even the valid entries.
    after = assert_ok(api.answers(attempt_id))["answers"]
    assert after == baseline
    assert sum(1 for answer in after if answer["answered"]) == 1


def test_batch_autosave_rejects_a_duplicate_question(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    question = questions[0]

    error = assert_error(
        api.autosave(
            attempt_id,
            [
                {"questionId": question["questionId"], "response": answer_for(question, variant=0)},
                {"questionId": question["questionId"], "response": answer_for(question, variant=1)},
            ],
        ),
        400,
        "VALIDATION_ERROR",
    )
    assert error["context"]["duplicateQuestionIds"] == [question["questionId"]]


def test_batch_autosave_rejects_an_empty_list(context: AppContext, api: ApiClient) -> None:
    attempt_id, _ = _attempt(context, api)
    assert_error(api.autosave(attempt_id, []), 400, "BAD_REQUEST")


def test_batch_autosave_can_clear_answers(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    assert_ok(
        api.autosave(
            attempt_id,
            [{"questionId": q["questionId"], "response": answer_for(q)} for q in questions],
        )
    )

    assert_ok(
        api.autosave(
            attempt_id, [{"questionId": questions[0]["questionId"], "response": None}]
        )
    )
    state = assert_ok(api.state(attempt_id))["state"]
    assert state["answeredCount"] == len(questions) - 1


def test_batch_size_limit_is_enforced(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    oversized = [
        {"questionId": questions[0]["questionId"], "response": None} for _ in range(501)
    ]
    error = assert_error(api.autosave(attempt_id, oversized), 400, "VALIDATION_ERROR")
    assert error["context"]["limit"] == 500


# ---------------------------------------------------------------------------
# Optimistic concurrency
# ---------------------------------------------------------------------------


def test_expected_revision_detects_a_concurrent_change(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)
    question = questions[0]

    assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question, variant=0)))
    # Another tab moved the answer on; revision is now 2.
    assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question, variant=1)))

    error = assert_error(
        api.save_answer(
            attempt_id,
            question["questionId"],
            answer_for(question, variant=2),
            expected_revision=1,
        ),
        409,
        "ANSWER_REVISION_CONFLICT",
    )
    assert error["context"]["currentRevision"] == 2
    assert error["context"]["expectedRevision"] == 1


def test_expected_revision_zero_matches_an_unanswered_question(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)
    question = questions[0]
    body = assert_ok(
        api.save_answer(
            attempt_id, question["questionId"], answer_for(question), expected_revision=0
        )
    )
    assert body["answer"]["revision"] == 1


# ---------------------------------------------------------------------------
# Reload / reconnection
# ---------------------------------------------------------------------------


def test_reload_returns_the_full_saved_state(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)

    # The learner answers some questions and flags one, then "closes the browser".
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    assert_ok(api.save_answer(attempt_id, questions[2]["questionId"], answer_for(questions[2])))
    assert_ok(api.set_flag(attempt_id, questions[3]["questionId"], True))
    assert_ok(api.set_cursor(attempt_id, 3))

    # On reconnection the client finds the attempt without knowing its id...
    active = assert_ok(api.active_attempt(QUIZ_ID))["attempt"]
    assert active["attemptId"] == attempt_id
    assert active["currentPosition"] == 3

    # ...and rebuilds everything from the server.
    answers = assert_ok(api.answers(attempt_id))
    assert answers["answeredCount"] == 2
    by_position = {answer["position"]: answer for answer in answers["answers"]}
    assert by_position[1]["answered"] is True
    assert by_position[2]["answered"] is False
    assert by_position[3]["answered"] is True

    flags = assert_ok(api.flags(attempt_id))
    assert flags["flaggedCount"] == 1
    assert next(flag for flag in flags["flags"] if flag["position"] == 4)["flagged"] is True

    state = assert_ok(api.state(attempt_id))["state"]
    assert state["answeredCount"] == 2
    assert state["flaggedCount"] == 1
    assert state["currentPosition"] == 3


def test_no_active_attempt_is_reported_clearly(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx)
    assert_error(api.active_attempt(QUIZ_ID), 404, "NO_ACTIVE_ATTEMPT")


def test_submitted_attempt_is_not_returned_as_active(context: AppContext, api: ApiClient) -> None:
    attempt_id, _ = _attempt(context, api)
    assert_ok(api.submit(attempt_id, idempotency_key="key-1"))
    assert_error(api.active_attempt(QUIZ_ID), 404, "NO_ACTIVE_ATTEMPT")


# ---------------------------------------------------------------------------
# Save-failure reporting (what the client's warning UI will consume)
# ---------------------------------------------------------------------------


def test_save_failure_responses_are_structured_and_actionable(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)

    response = api.save_answer(attempt_id, questions[0]["questionId"], {"selectedOptionId": "bad"})
    assert response.status_code == 422
    error = response.json()["error"]

    # Everything a client needs to show a persistent warning and offer manual retry.
    assert error["code"] == "INVALID_ANSWER"
    assert error["message"]
    assert error["retryable"] is False
    assert error["requestId"]
    assert error["timestamp"]
    assert "context" in error


def test_save_after_submission_is_rejected_as_non_retryable(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)
    assert_ok(api.submit(attempt_id, idempotency_key="key-1"))

    response = api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0]))
    error = assert_error(response, 409, "ATTEMPT_ALREADY_SUBMITTED")
    assert error["retryable"] is False


def test_audit_trail_records_each_accepted_save(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    question = questions[0]

    assert_ok(
        api.save_answer(attempt_id, question["questionId"], answer_for(question, variant=0))
    )
    assert_ok(
        api.save_answer(
            attempt_id, question["questionId"], answer_for(question, variant=1), source="AUTOSAVE"
        )
    )

    body = assert_ok(api.answer_revisions(attempt_id))
    assert body["count"] == 2
    assert [revision["revision"] for revision in body["revisions"]] == [1, 2]
    assert body["revisions"][0]["source"] == "MANUAL"
    assert body["revisions"][1]["source"] == "AUTOSAVE"
    assert all(revision["questionId"] == question["questionId"] for revision in body["revisions"])
