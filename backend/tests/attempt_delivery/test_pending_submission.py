"""Pending submission and retry.

The flow required by the specification: learner submits, the downstream hand-off fails,
the submission becomes PENDING, a retry completes it — and no duplicate record is ever
created.

The downstream dispatcher is a real dependency (a port), so these paths are reached by
supplying a failing implementation rather than by patching internals.
"""

from __future__ import annotations

from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import (
    AttemptStatus,
    SubmissionReason,
    SubmissionState,
)
from app.modules.attempt_delivery.models import AttemptSubmission
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import QUIZ_ID, answer_for, seed_world


def _ready_attempt(context: AppContext, api: ApiClient, count: int = 3) -> tuple[str, list[dict]]:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": count})
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    questions = assert_ok(api.questions(attempt_id))["questions"]
    assert_ok(
        api.autosave(
            attempt_id,
            [{"questionId": q["questionId"], "response": answer_for(q)} for q in questions],
        )
    )
    return attempt_id, questions


# ---------------------------------------------------------------------------
# Transient failure -> PENDING -> retry -> SUBMITTED
# ---------------------------------------------------------------------------


def test_transient_failure_leaves_a_pending_submission(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, _ = _ready_attempt(context, api)
    dispatcher.fail_transiently()

    error = assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    # The response tells the client exactly what state things are in and that a retry
    # is worthwhile.
    assert error["retryable"] is True
    assert error["context"]["submissionState"] == str(SubmissionState.PENDING)
    assert error["context"]["attemptStatus"] == str(AttemptStatus.SUBMISSION_PENDING)
    assert error["context"]["submissionId"]

    state = assert_ok(api.submission(attempt_id))
    assert state["attemptStatus"] == str(AttemptStatus.SUBMISSION_PENDING)
    assert state["submission"] is None
    assert state["pendingSubmission"]["state"] == str(SubmissionState.PENDING)
    assert state["pendingSubmission"]["failureCode"] == "DISPATCH_TRANSIENT_FAILURE"


def test_pending_attempt_is_locked_for_answers(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, questions = _ready_attempt(context, api)
    dispatcher.fail_transiently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    # The learner already committed, so their answers are frozen even though the
    # submission has not completed.
    assert_error(
        api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0], variant=1)),
        409,
        "ATTEMPT_SUBMISSION_PENDING",
    )
    assert_error(
        api.set_flag(attempt_id, questions[0]["questionId"], True), 409, "ATTEMPT_SUBMISSION_PENDING"
    )


def test_retry_completes_the_pending_submission(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, _ = _ready_attempt(context, api)

    dispatcher.fail_transiently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    # The downstream service recovers.
    dispatcher.succeed()
    body = assert_ok(api.retry_submission(attempt_id))

    assert body["submission"]["state"] == str(SubmissionState.SUBMITTED)
    assert body["submission"]["reason"] == str(SubmissionReason.LEARNER_CONFIRMED)
    # attempt_count records that this took two tries.
    assert body["submission"]["attemptCount"] == 2
    assert body["attempt"]["status"] == str(AttemptStatus.SUBMITTED)
    assert body["attempt"]["finalisedAt"] is not None

    # Still exactly one submission record.
    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1


def test_retry_does_not_create_a_duplicate_after_repeated_failures(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, _ = _ready_attempt(context, api)
    dispatcher.fail_transiently()

    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")
    for _ in range(3):
        assert_error(api.retry_submission(attempt_id), 502, "SUBMISSION_FAILED")

    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1

    pending = assert_ok(api.submission(attempt_id))["pendingSubmission"]
    assert pending["state"] == str(SubmissionState.PENDING)
    assert pending["attemptCount"] == 4

    dispatcher.succeed()
    final = assert_ok(api.retry_submission(attempt_id))
    assert final["submission"]["state"] == str(SubmissionState.SUBMITTED)
    assert final["submission"]["attemptCount"] == 5

    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1


def test_resubmitting_with_the_same_key_also_retries(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    # A client that simply repeats its original POST (rather than calling /retry) must
    # also converge, not create a second submission.
    attempt_id, _ = _ready_attempt(context, api)

    dispatcher.fail_transiently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    dispatcher.succeed()
    body = assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))
    assert body["submission"]["state"] == str(SubmissionState.SUBMITTED)

    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1


def test_a_new_key_is_refused_while_a_submission_is_pending(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, _ = _ready_attempt(context, api)
    dispatcher.fail_transiently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    dispatcher.succeed()
    # The client is directed to retry the existing submission rather than start another.
    assert_error(
        api.submit(attempt_id, idempotency_key="submit-2"), 409, "ATTEMPT_SUBMISSION_PENDING"
    )

    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1


def test_retry_of_a_completed_submission_replays_the_response(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, _ = _ready_attempt(context, api)
    original = assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))

    replay = assert_ok(api.retry_submission(attempt_id))
    assert replay["idempotentReplay"] is True
    assert replay["submission"] == original["submission"]


def test_retry_without_a_pending_submission_is_reported(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id, _ = _ready_attempt(context, api)
    assert_error(api.retry_submission(attempt_id), 409, "NO_PENDING_SUBMISSION")


def test_retry_can_target_a_specific_key(context: AppContext, api: ApiClient, dispatcher) -> None:
    attempt_id, _ = _ready_attempt(context, api)
    dispatcher.fail_transiently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-abc"), 502, "SUBMISSION_FAILED")

    dispatcher.succeed()
    body = assert_ok(api.retry_submission(attempt_id, idempotency_key="submit-abc"))
    assert body["submission"]["state"] == str(SubmissionState.SUBMITTED)


def test_retry_with_an_unknown_key_is_reported(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, _ = _ready_attempt(context, api)
    dispatcher.fail_transiently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    assert_error(
        api.retry_submission(attempt_id, idempotency_key="never-used"),
        409,
        "NO_PENDING_SUBMISSION",
    )


def test_pending_submission_preserves_the_frozen_answers(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, questions = _ready_attempt(context, api)
    before = assert_ok(api.answers(attempt_id))["answers"]

    dispatcher.fail_transiently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    dispatcher.succeed()
    assert_ok(api.retry_submission(attempt_id))

    # The retry submits exactly what the learner left, not a re-derived set.
    after = assert_ok(api.answers(attempt_id))["answers"]
    assert [answer["response"] for answer in after] == [answer["response"] for answer in before]
    assert dispatcher.calls[-1].answered_count == len(questions)


# ---------------------------------------------------------------------------
# Permanent failure
# ---------------------------------------------------------------------------


def test_permanent_failure_releases_the_attempt(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, questions = _ready_attempt(context, api)
    dispatcher.fail_permanently()

    error = assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")
    assert error["retryable"] is False
    assert error["context"]["submissionState"] == str(SubmissionState.FAILED)
    assert error["context"]["attemptStatus"] == str(AttemptStatus.ACTIVE)

    # The learner is not stranded: the attempt is workable again.
    attempt = assert_ok(api.get_attempt(attempt_id))["attempt"]
    assert attempt["status"] == str(AttemptStatus.ACTIVE)
    assert attempt["submittedAt"] is None
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0], variant=1)))

    state = assert_ok(api.submission(attempt_id))
    assert state["submission"] is None
    assert state["pendingSubmission"] is None
    assert state["history"][0]["state"] == str(SubmissionState.FAILED)


def test_a_failed_submission_can_be_submitted_again(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    attempt_id, _ = _ready_attempt(context, api)

    dispatcher.fail_permanently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    dispatcher.succeed()
    body = assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))
    assert body["submission"]["state"] == str(SubmissionState.SUBMITTED)

    # Reusing the same key reuses the record rather than adding one.
    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptSubmission).count() == 1


def test_permanent_failure_does_not_consume_an_extra_attempt(
    context: AppContext, api: ApiClient, dispatcher
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 2, "maxAttempts": 1})
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    dispatcher.fail_permanently()
    assert_error(api.submit(attempt_id, idempotency_key="submit-1"), 502, "SUBMISSION_FAILED")

    # The single allowed attempt is still the same attempt, still usable.
    assert len(assert_ok(api.list_attempts(QUIZ_ID))["attempts"]) == 1
    dispatcher.succeed()
    assert_ok(api.submit(attempt_id, idempotency_key="submit-1"))


# ---------------------------------------------------------------------------
# Expiry interaction
# ---------------------------------------------------------------------------


def test_expiry_with_a_failing_downstream_still_locks_the_attempt(
    context: AppContext, api: ApiClient, dispatcher, clock
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 2, "timeLimitSeconds": 60})
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    questions = assert_ok(api.questions(attempt_id))["questions"]
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))

    dispatcher.fail_transiently()
    clock.advance(seconds=61)

    # A read settles the expiry. The local commit is durable even though the hand-off
    # failed, so the learner cannot keep answering.
    attempt = assert_ok(api.get_attempt(attempt_id))["attempt"]
    assert attempt["status"] == str(AttemptStatus.SUBMISSION_PENDING)
    assert attempt["submissionReason"] == str(SubmissionReason.TIME_EXPIRED)
    assert_error(
        api.save_answer(attempt_id, questions[1]["questionId"], answer_for(questions[1])),
        409,
        "ATTEMPT_SUBMISSION_PENDING",
    )

    dispatcher.succeed()
    body = assert_ok(api.retry_submission(attempt_id))
    assert body["submission"]["state"] == str(SubmissionState.SUBMITTED)
    assert body["submission"]["reason"] == str(SubmissionReason.TIME_EXPIRED)
    assert body["submission"]["answeredCount"] == 1
