"""End-to-end walkthrough of the attempt lifecycle over HTTP.

These tests follow the lifecycle in the specification from start to finish, using only
the public API, so they demonstrate that a future frontend has everything it needs. They
run against the shared demo dataset (``tests.support.demo_world``) rather than a fixture tuned
for the assertion at hand, which also
verifies that the seeded configuration is coherent.
"""

from __future__ import annotations

from app.core.time import FixedClock
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import (
    AttemptStatus,
    QuestionType,
    SubmissionReason,
    SubmissionState,
)
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.demo_world import seed_demo_world
from tests.support.fixtures import QUIZ_ID, answer_for


def _seed_demo(context: AppContext) -> None:
    with context.unit_of_work() as ctx:
        seed_demo_world(ctx)


def test_full_lifecycle_learner_confirms_submission(
    context: AppContext, api: ApiClient, clock: FixedClock, dispatcher
) -> None:
    _seed_demo(context)

    # 1. The learner checks whether they may attempt the quiz.
    eligibility = assert_ok(api.eligibility(QUIZ_ID))["eligibility"]
    assert eligibility["eligible"] is True
    assert eligibility["enrolled"] is True
    assert eligibility["attemptsRemaining"] == 3

    # 2. They start an attempt. The active configuration version is locked onto it.
    created = assert_ok(api.create_attempt(QUIZ_ID), 201)
    attempt = created["attempt"]
    attempt_id = attempt["attemptId"]
    assert attempt["status"] == str(AttemptStatus.ACTIVE)
    assert attempt["configuration"]["configurationVersionId"] == "cfg-fire-safety-v1"
    assert attempt["totalQuestions"] == 6

    # The quotas in the seeded configuration are honoured.
    assert created["delivery"]["questionTypeCounts"] == {
        str(QuestionType.SINGLE_CHOICE): 2,
        str(QuestionType.TRUE_FALSE): 1,
        str(QuestionType.MULTI_SELECT): 1,
        str(QuestionType.DRAG_TO_ORDER): 1,
        str(QuestionType.SCENARIO): 1,
    }

    # 3. The paper is fetched. No correct answers are exposed, and the retired
    #    question is absent.
    questions_response = api.questions(attempt_id)
    questions = assert_ok(questions_response)["questions"]
    assert len(questions) == 6
    assert "isCorrect" not in questions_response.text
    assert "q-sc-retired" not in questions_response.text

    # 4. The learner answers the first three questions.
    for question in questions[:3]:
        assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question)))

    # 5. Autosave fires 30 seconds later and flushes everything, including a change.
    clock.advance(seconds=30)
    autosave = assert_ok(
        api.autosave(
            attempt_id,
            [
                {"questionId": questions[0]["questionId"], "response": answer_for(questions[0])},
                {"questionId": questions[3]["questionId"], "response": answer_for(questions[3])},
            ],
        )
    )
    assert autosave["savedCount"] == 2
    # Question 1 was unchanged; question 4 is new.
    assert autosave["changedCount"] == 1

    # 6. A question is flagged for review.
    assert_ok(api.set_flag(attempt_id, questions[5]["questionId"], True))

    # 7. The learner's connection drops. They reconnect and rebuild from the server.
    active = assert_ok(api.active_attempt(QUIZ_ID))["attempt"]
    assert active["attemptId"] == attempt_id

    state = assert_ok(api.state(attempt_id))["state"]
    assert state["answeredCount"] == 4
    assert state["unansweredCount"] == 2
    assert state["flaggedCount"] == 1
    assert state["timing"]["remainingSeconds"] == 1800 - 30

    # 8. They finish the remaining questions and unflag the one they revisited.
    for question in questions[4:]:
        assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question)))
    assert_ok(api.unflag(attempt_id, questions[5]["questionId"]))

    # 9. Submission preparation. This must not submit.
    preview = assert_ok(api.preview_submission(attempt_id))["preview"]
    assert preview["canSubmit"] is True
    assert preview["unansweredCount"] == 0
    assert preview["requiresConfirmation"] is True
    assert assert_ok(api.get_attempt(attempt_id))["attempt"]["status"] == str(AttemptStatus.ACTIVE)

    # 10. Confirmed submission.
    submitted = assert_ok(
        api.submit(attempt_id, idempotency_key=preview["suggestedIdempotencyKey"])
    )
    assert submitted["submission"]["state"] == str(SubmissionState.SUBMITTED)
    assert submitted["submission"]["reason"] == str(SubmissionReason.LEARNER_CONFIRMED)
    assert submitted["summary"] == {
        "totalQuestions": 6,
        "answeredCount": 6,
        "completeCount": 6,
        "unansweredCount": 0,
    }
    assert submitted["attempt"]["status"] == str(AttemptStatus.SUBMITTED)
    assert len(dispatcher.calls) == 1

    # 11. The attempt is locked.
    assert_error(
        api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0], variant=1)),
        409,
        "ATTEMPT_ALREADY_SUBMITTED",
    )

    # 12. A repeated submit (double-click / retry) is safe.
    replay = assert_ok(api.submit(attempt_id, idempotency_key=preview["suggestedIdempotencyKey"]))
    assert replay["idempotentReplay"] is True
    assert len(assert_ok(api.submission(attempt_id))["history"]) == 1
    assert len(dispatcher.calls) == 1

    # 13. A new attempt may be started; the allowance has decreased by one.
    after = assert_ok(api.eligibility(QUIZ_ID))["eligibility"]
    assert after["attemptsUsed"] == 1
    assert after["attemptsRemaining"] == 2
    assert after["eligible"] is True


def test_full_lifecycle_time_expiry(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    _seed_demo(context)

    # Shorten the time limit for this walkthrough.
    with context.unit_of_work() as ctx:
        from tests.support.fixtures import publish_configuration

        publish_configuration(
            ctx,
            version=2,
            configuration_version_id="cfg-fire-safety-v2",
            activated_at="2026-02-01T00:00:00Z",
            rules={
                "questionCount": 4,
                "timeLimitSeconds": 120,
                "passMarkPercentage": 70,
                "maxAttempts": 3,
                "randomiseQuestionOrder": False,
                "randomiseOptionOrder": False,
                "allowIncompleteSubmission": True,
            },
        )

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    questions = assert_ok(api.questions(attempt_id))["questions"]

    # Two answers land in time.
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    clock.advance(seconds=60)
    assert_ok(api.save_answer(attempt_id, questions[1]["questionId"], answer_for(questions[1])))

    # Sixty seconds left, and the client is told so.
    assert assert_ok(api.timing(attempt_id))["timing"]["remainingSeconds"] == 60

    # Time runs out while the learner is still working.
    clock.advance(seconds=61)

    # The next request settles the expiry and submits the saved answers.
    final = assert_ok(api.get_attempt(attempt_id))["attempt"]
    assert final["status"] == str(AttemptStatus.SUBMITTED)
    assert final["submissionReason"] == str(SubmissionReason.TIME_EXPIRED)

    submission = assert_ok(api.submission(attempt_id))
    assert submission["submission"]["answeredCount"] == 2
    assert submission["submission"]["totalQuestions"] == 4

    # Late writes are refused, and the record is stable however often it is read.
    assert_error(
        api.save_answer(attempt_id, questions[2]["questionId"], answer_for(questions[2])),
        409,
        "ATTEMPT_ALREADY_SUBMITTED",
    )
    for _ in range(3):
        assert len(assert_ok(api.submission(attempt_id))["history"]) == 1


def test_full_lifecycle_one_at_a_time_with_pending_submission(
    context: AppContext, api: ApiClient, clock: FixedClock, dispatcher
) -> None:
    _seed_demo(context)

    with context.unit_of_work() as ctx:
        from app.modules.attempt_delivery.domain.enums import QuestionPresentation
        from tests.support.fixtures import publish_configuration

        publish_configuration(
            ctx,
            version=3,
            configuration_version_id="cfg-fire-safety-v3",
            activated_at="2026-02-15T00:00:00Z",
            rules={
                "questionCount": 3,
                "timeLimitSeconds": 900,
                "passMarkPercentage": 70,
                "maxAttempts": 3,
                "questionPresentation": str(QuestionPresentation.ONE_AT_A_TIME),
                "randomiseQuestionOrder": False,
                "randomiseOptionOrder": False,
                "allowIncompleteSubmission": True,
            },
        )

    created = assert_ok(api.create_attempt(QUIZ_ID), 201)
    attempt_id = created["attempt"]["attemptId"]
    assert created["delivery"]["questionsUrl"].endswith("/questions/current")

    # The whole paper is not available in this mode.
    assert_error(api.questions(attempt_id), 409, "QUESTION_PRESENTATION_VIOLATION")

    # The learner walks the paper one question at a time, saving as they go.
    for position in (1, 2, 3):
        body = assert_ok(api.question_at(attempt_id, position))
        question = body["question"]
        assert_ok(api.save_answer(attempt_id, question["questionId"], answer_for(question)))
        assert_ok(api.set_cursor(attempt_id, position))
        clock.advance(seconds=30)

    assert assert_ok(api.current_question(attempt_id))["question"]["position"] == 3

    # The grading service is down when they submit.
    dispatcher.fail_transiently()
    error = assert_error(api.submit(attempt_id, idempotency_key="e2e-submit"), 502, "SUBMISSION_FAILED")
    assert error["retryable"] is True

    # The attempt is locked and the submission is pending.
    pending = assert_ok(api.submission(attempt_id))
    assert pending["attemptStatus"] == str(AttemptStatus.SUBMISSION_PENDING)
    assert pending["pendingSubmission"]["state"] == str(SubmissionState.PENDING)

    # The service recovers and the learner retries.
    dispatcher.succeed()
    completed = assert_ok(api.retry_submission(attempt_id))
    assert completed["submission"]["state"] == str(SubmissionState.SUBMITTED)
    assert completed["submission"]["attemptCount"] == 2
    assert completed["submission"]["answeredCount"] == 3

    # Exactly one submission record for the attempt.
    assert len(assert_ok(api.submission(attempt_id))["history"]) == 1
