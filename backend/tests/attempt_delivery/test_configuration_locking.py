"""Configuration version locking.

The central guarantee: an attempt captures the configuration version active when it was
created and keeps using that snapshot. Publishing a new version, or withdrawing the one
in use, must not disturb an attempt in flight.
"""

from __future__ import annotations

from app.core.time import FixedClock
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import QuestionPresentation
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import (
    LEARNER_ID,
    QUIZ_ID,
    publish_configuration,
    seed_world,
)


def test_new_version_does_not_change_a_running_attempt(
    context: AppContext, api: ApiClient
) -> None:
    # Version 1: 70% pass mark, 30 minutes.
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"passMarkPercentage": 70, "timeLimitSeconds": 1800, "questionCount": 5},
        )

    attempt = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    attempt_id = attempt["attemptId"]
    assert attempt["configuration"]["version"] == 1
    assert attempt["configuration"]["passMarkPercentage"] == 70
    assert attempt["configuration"]["timeLimitSeconds"] == 1800
    original_expiry = attempt["expiresAt"]

    # An administrator publishes version 2: 90% pass mark, 5 minutes, 3 questions.
    with context.unit_of_work() as ctx:
        published = publish_configuration(
            ctx,
            version=2,
            activated_at="2026-03-01T09:05:00Z",
            rules={"passMarkPercentage": 90, "timeLimitSeconds": 300, "questionCount": 3},
        )
        assert published.version == 2

    # The running attempt is untouched: same version, same rules, same deadline.
    reloaded = assert_ok(api.get_attempt(attempt_id))["attempt"]
    assert reloaded["configuration"]["version"] == 1
    assert reloaded["configuration"]["passMarkPercentage"] == 70
    assert reloaded["configuration"]["timeLimitSeconds"] == 1800
    assert reloaded["expiresAt"] == original_expiry
    assert reloaded["totalQuestions"] == 5
    assert reloaded["timing"]["timeLimitSeconds"] == 1800


def test_the_next_attempt_uses_the_new_version(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"passMarkPercentage": 70, "questionCount": 5, "maxAttempts": None})

    first = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    assert first["configuration"]["version"] == 1
    assert_ok(api.submit(first["attemptId"], idempotency_key="k1"))

    with context.unit_of_work() as ctx:
        publish_configuration(
            ctx, version=2, rules={"passMarkPercentage": 90, "questionCount": 3, "maxAttempts": None}
        )

    second = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    assert second["configuration"]["version"] == 2
    assert second["configuration"]["passMarkPercentage"] == 90
    assert second["totalQuestions"] == 3

    # The earlier attempt still reports version 1.
    assert assert_ok(api.get_attempt(first["attemptId"]))["attempt"]["configuration"]["version"] == 1


def test_attempt_survives_its_version_being_withdrawn(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        configuration = seed_world(ctx, rules={"questionCount": 4})

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    # UC-01 deletes the version outright. The snapshot on the attempt is the only
    # source UC-03 reads during an attempt, so the attempt keeps working.
    with context.unit_of_work() as ctx:
        ctx.seedable_configurations.delete_version(configuration.configuration_version_id)
        assert ctx.configurations.get_active_configuration(QUIZ_ID) is None

    reloaded = assert_ok(api.get_attempt(attempt_id))["attempt"]
    assert reloaded["configuration"]["version"] == 1
    assert reloaded["totalQuestions"] == 4

    # Answering and submitting still work.
    questions = assert_ok(api.questions(attempt_id))["questions"]
    assert len(questions) == 4
    assert_ok(api.submit(attempt_id, idempotency_key="k-withdrawn"))


def test_delivery_mode_is_locked_at_creation(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionPresentation": str(QuestionPresentation.ALL_AT_ONCE)})

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    assert_ok(api.questions(attempt_id))  # permitted under ALL_AT_ONCE

    with context.unit_of_work() as ctx:
        publish_configuration(
            ctx, version=2, rules={"questionPresentation": str(QuestionPresentation.ONE_AT_A_TIME)}
        )

    # Still all-at-once for this attempt.
    body = assert_ok(api.questions(attempt_id))
    assert body["questionPresentation"] == str(QuestionPresentation.ALL_AT_ONCE)


def test_expiry_uses_the_locked_time_limit_not_the_current_one(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"timeLimitSeconds": 600})

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    # Shorten the limit drastically in UC-01.
    with context.unit_of_work() as ctx:
        publish_configuration(ctx, version=2, rules={"timeLimitSeconds": 10})

    # Past the *new* limit but well inside the locked one: still active.
    clock.advance(seconds=60)
    timing = assert_ok(api.timing(attempt_id))["timing"]
    assert timing["expired"] is False
    assert timing["remainingSeconds"] == 540
    assert timing["timeLimitSeconds"] == 600


def test_incoherent_configuration_is_rejected_before_persistence(
    context: AppContext, api: ApiClient
) -> None:
    # Quotas that do not sum to questionCount cannot produce a coherent paper.
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionCount": 10,
                "questionTypeQuotas": [
                    {"type": "SINGLE_CHOICE", "count": 3},
                    {"type": "TRUE_FALSE", "count": 2},
                ],
            },
        )

    error = assert_error(api.create_attempt(QUIZ_ID), 422, "INVALID_CONFIGURATION")
    assert error["context"]["quotaTotal"] == 5
    assert error["context"]["questionCount"] == 10

    with context.unit_of_work() as ctx:
        assert ctx.attempts_repo.count_for_learner_and_quiz(LEARNER_ID, QUIZ_ID) == 0


def test_non_positive_question_count_is_rejected(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 0})

    assert_error(api.create_attempt(QUIZ_ID), 422, "INVALID_CONFIGURATION")


def test_out_of_range_pass_mark_is_rejected(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"passMarkPercentage": 150})

    assert_error(api.create_attempt(QUIZ_ID), 422, "INVALID_CONFIGURATION")


def test_configuration_defaults_are_normalised_into_the_snapshot(
    context: AppContext, api: ApiClient
) -> None:
    # UC-01 omits the optional rules entirely.
    with context.unit_of_work() as ctx:
        from tests.support.fixtures import seed_enrolment, seed_question_bank, seed_quiz

        seed_quiz(ctx)
        seed_enrolment(ctx)
        seed_question_bank(ctx)
        ctx.seedable_configurations.publish_version(
            configuration_version_id="cfg-minimal",
            quiz_id=QUIZ_ID,
            course_id="course-fire-safety",
            version=1,
            activated_at="2026-01-01T00:00:00Z",
            rules={
                "questionCount": 3,
                "passMarkPercentage": 50,
                "configurationVersionId": "cfg-minimal",
                "quizId": QUIZ_ID,
                "courseId": "course-fire-safety",
                "version": 1,
                "activatedAt": "2026-01-01T00:00:00Z",
            },
        )

    attempt = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    configuration = attempt["configuration"]

    # Every value the attempt depends on is fully specified in the snapshot.
    assert configuration["questionPresentation"] == str(QuestionPresentation.ALL_AT_ONCE)
    assert configuration["randomiseQuestionOrder"] is False
    assert configuration["randomiseOptionOrder"] is False
    assert configuration["allowIncompleteSubmission"] is True
    assert configuration["timeLimitSeconds"] is None
    assert configuration["maxAttempts"] is None
