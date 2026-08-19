"""Submission: preparation, confirmation, locking and idempotency."""

from __future__ import annotations

from app.core.time import FixedClock
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import (
    AttemptStatus,
    SubmissionReason,
    SubmissionState,
)
from app.modules.attempt_delivery.models import AttemptSubmission
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import QUIZ_ID, answer_for, seed_world


def _attempt(
    context: AppContext, api: ApiClient, *, count: int = 3, **rules: object
) -> tuple[str, list[dict]]:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": count, **rules})
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    return attempt_id, assert_ok(api.questions(attempt_id))["questions"]


def _answer_all(api: ApiClient, attempt_id: str, questions: list[dict]) -> None:
    assert_ok(
        api.autosave(
            attempt_id,
            [{"questionId": q["questionId"], "response": answer_for(q)} for q in questions],
        )
    )


# ---------------------------------------------------------------------------
# Preparation (preview) is separate from the commit
# ---------------------------------------------------------------------------


def test_preview_summarises_what_would_be_submitted(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api, count=4)
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    assert_ok(api.set_flag(attempt_id, questions[2]["questionId"], True))

    preview = assert_ok(api.preview_submission(attempt_id))["preview"]

    assert preview["totalQuestions"] == 4
    assert preview["answeredCount"] == 1
    assert preview["unansweredCount"] == 3
    assert [entry["position"] for entry in preview["unanswered"]] == [2, 3, 4]
    assert [entry["position"] for entry in preview["flagged"]] == [3]
    assert preview["canSubmit"] is True
    assert preview["requiresConfirmation"] is True
    assert preview["suggestedIdempotencyKey"]
    codes = {warning["code"] for warning in preview["warnings"]}
    assert {"UNANSWERED_QUESTIONS", "FLAGGED_QUESTIONS"} <= codes


def test_preview_never_submits(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    for _ in range(5):
        assert_ok(api.preview_submission(attempt_id))

    attempt = assert_ok(api.get_attempt(attempt_id))["attempt"]
    assert attempt["status"] == str(AttemptStatus.ACTIVE)
    assert attempt["submittedAt"] is None
    # No submission record was created by previewing.
    assert assert_ok(api.submission(attempt_id))["history"] == []


def test_preview_blocks_when_completeness_is_required(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api, allowIncompleteSubmission=False)
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))

    preview = assert_ok(api.preview_submission(attempt_id))["preview"]
    assert preview["canSubmit"] is False
    assert preview["allowIncompleteSubmission"] is False
    assert [blocker["code"] for blocker in preview["blockers"]] == [
        "INCOMPLETE_SUBMISSION_NOT_ALLOWED"
    ]


def test_preview_warns_when_time_is_nearly_up(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, _ = _attempt(context, api, timeLimitSeconds=300)
    clock.advance(seconds=270)

    preview = assert_ok(api.preview_submission(attempt_id))["preview"]
    assert "TIME_ALMOST_ELAPSED" in {warning["code"] for warning in preview["warnings"]}
    assert preview["timing"]["remainingSeconds"] == 30


# ---------------------------------------------------------------------------
# Confirmed submission
# ---------------------------------------------------------------------------


def test_confirmed_submission_locks_the_attempt(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    body = assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))

    assert body["submission"]["state"] == str(SubmissionState.SUBMITTED)
    assert body["submission"]["reason"] == str(SubmissionReason.LEARNER_CONFIRMED)
    assert body["submission"]["answeredCount"] == 3
    assert body["submission"]["totalQuestions"] == 3
    assert body["submission"]["completedAt"] is not None
    assert body["idempotentReplay"] is False

    assert body["attempt"]["status"] == str(AttemptStatus.SUBMITTED)
    assert body["attempt"]["submittedAt"] is not None
    assert body["attempt"]["finalisedAt"] is not None
    assert body["summary"] == {
        "totalQuestions": 3,
        "answeredCount": 3,
        "completeCount": 3,
        "unansweredCount": 0,
    }


def test_submission_requires_explicit_confirmation(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    assert_error(
        api.submit(attempt_id, confirmed=False, idempotency_key="k"),
        400,
        "SUBMISSION_NOT_CONFIRMED",
    )

    # Still active: an unconfirmed request changes nothing.
    assert assert_ok(api.get_attempt(attempt_id))["attempt"]["status"] == str(AttemptStatus.ACTIVE)
    assert assert_ok(api.submission(attempt_id))["history"] == []


def test_submission_missing_the_confirmed_field_is_rejected(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, _ = _attempt(context, api)
    response = api.request("POST", f"/api/v1/attempts/{attempt_id}/submission", json={})
    assert_error(response, 400, "BAD_REQUEST")


def test_answer_updates_are_rejected_after_submission(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)
    assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))

    error = assert_error(
        api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0], variant=1)),
        409,
        "ATTEMPT_ALREADY_SUBMITTED",
    )
    assert error["context"]["attemptId"] == attempt_id

    # Batch autosave, clearing and the cursor are refused too.
    assert_error(
        api.autosave(attempt_id, [{"questionId": questions[0]["questionId"], "response": None}]),
        409,
        "ATTEMPT_ALREADY_SUBMITTED",
    )
    assert_error(api.clear_answer(attempt_id, questions[0]["questionId"]), 409, "ATTEMPT_ALREADY_SUBMITTED")
    assert_error(api.set_cursor(attempt_id, 2), 409, "ATTEMPT_ALREADY_SUBMITTED")


def test_submitted_answers_remain_readable(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)
    before = assert_ok(api.answers(attempt_id))["answers"]

    assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))

    after = assert_ok(api.answers(attempt_id))["answers"]
    assert [answer["response"] for answer in after] == [answer["response"] for answer in before]


def test_incomplete_submission_is_refused_when_configured(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api, allowIncompleteSubmission=False)
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))

    error = assert_error(
        api.submit(attempt_id, idempotency_key="submit-1"), 409, "ATTEMPT_NOT_SUBMITTABLE"
    )
    assert error["context"]["unansweredCount"] == 2
    assert error["context"]["allowIncompleteSubmission"] is False

    # Still active, and no submission record was created.
    assert assert_ok(api.get_attempt(attempt_id))["attempt"]["status"] == str(AttemptStatus.ACTIVE)
    assert assert_ok(api.submission(attempt_id))["history"] == []

    # Completing the paper unblocks it.
    _answer_all(api, attempt_id, questions)
    assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))


def test_incomplete_submission_is_allowed_by_default(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))

    body = assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))
    assert body["summary"]["unansweredCount"] == 2
    assert body["submission"]["answeredCount"] == 1


def test_submission_hands_off_the_frozen_answers(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)
    assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))

    assert len(dispatcher.calls) == 1
    request = dispatcher.calls[0]
    assert request.attempt_id == attempt_id
    assert request.answered_count == 3
    assert request.total_questions == 3
    assert request.submission_reason == str(SubmissionReason.LEARNER_CONFIRMED)
    assert [answer.position for answer in request.answers] == [1, 2, 3]
    assert all(answer.answered for answer in request.answers)


def test_downstream_reference_is_recorded(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    body = assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))
    assert body["submission"]["downstreamReference"] == "grading-ref-1"

    stored = assert_ok(api.submission(attempt_id))["submission"]
    assert stored["downstreamReference"] == "grading-ref-1"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_double_click_produces_one_submission(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    first = assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))
    second = assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))

    assert first["idempotentReplay"] is False
    assert second["idempotentReplay"] is True
    # The replay is the original response, byte for byte apart from the replay flag.
    assert second["submission"] == first["submission"]
    assert second["attempt"] == first["attempt"]

    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1


def test_many_retries_produce_one_submission(context: AppContext, api: ApiClient, dispatcher) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    responses = [api.submit(attempt_id, idempotency_key="submit-1") for _ in range(8)]
    assert all(response.status_code == 200 for response in responses)

    # One submission record, one attempt row, one downstream hand-off.
    submission = assert_ok(api.submission(attempt_id))
    assert len(submission["history"]) == 1
    assert len(dispatcher.calls) == 1
    assert len(assert_ok(api.list_attempts(QUIZ_ID))["attempts"]) == 1


def test_idempotency_key_may_come_from_the_header(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    first = assert_ok(api.submit(attempt_id, header_key="header-key-1"))
    second = assert_ok(api.submit(attempt_id, header_key="header-key-1"))
    assert first["idempotentReplay"] is False
    assert second["idempotentReplay"] is True


def test_omitting_the_key_still_collapses_duplicates(
    context: AppContext, api: ApiClient
) -> None:
    # Even a naive client that sends no key is protected by the attempt-derived default.
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    assert assert_ok(api.submit(attempt_id))["idempotentReplay"] is False
    assert assert_ok(api.submit(attempt_id))["idempotentReplay"] is True

    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1


def test_a_different_key_on_a_submitted_attempt_is_a_duplicate(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)
    assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))

    error = assert_error(
        api.submit(attempt_id, idempotency_key="submit-2"), 409, "DUPLICATE_SUBMISSION"
    )
    assert error["context"]["existingIdempotencyKey"] == "submit-1"

    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1


def test_key_reused_for_a_different_operation_is_rejected(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    # The time-expiry submission uses a deterministic key. Reusing that key for a
    # learner-confirmed submission is a different logical operation.
    attempt_id, questions = _attempt(context, api, timeLimitSeconds=60)
    _answer_all(api, attempt_id, questions)
    clock.advance(seconds=61)
    assert_ok(api.get_attempt(attempt_id))  # settles the expiry

    assert_error(
        api.submit(attempt_id, idempotency_key=f"time-expiry:{attempt_id}"),
        409,
        "IDEMPOTENCY_KEY_REUSED",
    )


def test_submission_state_endpoint_reports_history(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)
    assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))

    body = assert_ok(api.submission(attempt_id))
    assert body["attemptStatus"] == str(AttemptStatus.SUBMITTED)
    assert body["submissionReason"] == str(SubmissionReason.LEARNER_CONFIRMED)
    assert body["pendingSubmission"] is None
    assert body["submission"]["state"] == str(SubmissionState.SUBMITTED)
    assert body["submission"]["attemptCount"] == 1
    assert len(body["history"]) == 1


def test_another_learner_cannot_submit_the_attempt(context: AppContext, api: ApiClient) -> None:
    attempt_id, questions = _attempt(context, api)
    _answer_all(api, attempt_id, questions)

    from tests.support.fixtures import OTHER_LEARNER_ID

    assert_error(
        api.as_learner(OTHER_LEARNER_ID).submit(attempt_id, idempotency_key="hijack"),
        404,
        "ATTEMPT_NOT_FOUND",
    )
    assert assert_ok(api.get_attempt(attempt_id))["attempt"]["status"] == str(AttemptStatus.ACTIVE)
