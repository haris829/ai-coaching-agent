"""Attempt creation, enrolment validation and attempt limits."""

from __future__ import annotations

import pytest

from app.core.time import FixedClock, parse_instant
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import AttemptStatus, EnrolmentStatus
from app.modules.attempt_delivery.models import AttemptQuestion, QuizAttempt
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import (
    LEARNER_ID,
    OTHER_LEARNER_ID,
    QUIZ_ID,
    publish_configuration,
    seed_enrolment,
    seed_question_bank,
    seed_quiz,
    seed_world,
)

# ---------------------------------------------------------------------------
# Attempt creation
# ---------------------------------------------------------------------------


def test_enrolled_learner_creates_attempt(api: ApiClient, seeded: dict) -> None:
    response = api.create_attempt(QUIZ_ID)
    body = assert_ok(response, 201)

    attempt = body["attempt"]
    assert attempt["status"] == str(AttemptStatus.ACTIVE)
    assert attempt["learnerId"] == LEARNER_ID
    assert attempt["quizId"] == QUIZ_ID
    assert attempt["attemptNumber"] == 1
    assert attempt["totalQuestions"] == 5
    assert attempt["currentPosition"] == 1
    assert response.headers["Location"] == f"/api/v1/attempts/{attempt['attemptId']}"


def test_attempt_records_the_configuration_version(api: ApiClient, seeded: dict) -> None:
    body = assert_ok(api.create_attempt(QUIZ_ID), 201)
    attempt = body["attempt"]

    expected = seeded["configuration"]
    assert attempt["configurationVersionId"] == expected.configuration_version_id
    assert attempt["configuration"]["configurationVersionId"] == expected.configuration_version_id
    assert attempt["configuration"]["version"] == 1
    # The effective rules are surfaced from the snapshot, not re-read from UC-01.
    assert attempt["configuration"]["passMarkPercentage"] == 70
    assert attempt["configuration"]["timeLimitSeconds"] == 1800


def test_start_time_and_expiry_are_persisted_from_the_server_clock(
    api: ApiClient, seeded: dict, clock: FixedClock
) -> None:
    body = assert_ok(api.create_attempt(QUIZ_ID), 201)
    attempt = body["attempt"]

    assert parse_instant(attempt["startedAt"]) == clock.now()
    # expires_at is derived server-side from started_at + the locked time limit.
    assert (parse_instant(attempt["expiresAt"]) - clock.now()).total_seconds() == 1800


def test_untimed_configuration_produces_no_expiry(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"timeLimitSeconds": None})

    attempt = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    assert attempt["expiresAt"] is None
    assert attempt["timing"]["timed"] is False
    assert attempt["timing"]["remainingSeconds"] is None


def test_attempt_and_questions_are_committed_together(
    context: AppContext, api: ApiClient, seeded: dict
) -> None:
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    with context.unit_of_work() as ctx:
        stored = ctx.session.get(QuizAttempt, attempt_id)
        assert stored is not None
        questions = ctx.attempt_questions_repo.list_for_attempt(attempt_id)
        assert len(questions) == stored.total_questions == 5
        # Positions are dense and 1-based, which the navigation endpoints rely on.
        assert [question.position for question in questions] == [1, 2, 3, 4, 5]


def test_a_second_attempt_is_refused_while_one_is_open(api: ApiClient, seeded: dict) -> None:
    first = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]

    error = assert_error(api.create_attempt(QUIZ_ID), 409, "ACTIVE_ATTEMPT_EXISTS")
    assert error["context"]["activeAttemptId"] == first["attemptId"]


def test_unknown_quiz_is_rejected(api: ApiClient, seeded: dict) -> None:
    assert_error(api.create_attempt("quiz-does-not-exist"), 404, "QUIZ_NOT_FOUND")


def test_unavailable_quiz_is_rejected(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx)
        seed_quiz(ctx, available=False, reason="ARCHIVED")

    error = assert_error(api.create_attempt(QUIZ_ID), 409, "QUIZ_NOT_AVAILABLE")
    assert error["context"]["reason"] == "ARCHIVED"


def test_quiz_without_an_active_configuration_is_rejected(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_quiz(ctx)
        seed_enrolment(ctx)
        seed_question_bank(ctx)
        # A version exists but is not active, so there is nothing to lock.
        publish_configuration(ctx, version=1, activate=False)

    assert_error(api.create_attempt(QUIZ_ID), 409, "CONFIGURATION_VERSION_UNAVAILABLE")


def test_creation_requires_a_learner_identity(api: ApiClient, seeded: dict) -> None:
    response = api.request("POST", "/api/v1/attempts", json={"quizId": QUIZ_ID}, authenticated=False)
    assert_error(response, 401, "UNAUTHENTICATED")


def test_creation_rejects_a_missing_quiz_id(api: ApiClient, seeded: dict) -> None:
    assert_error(api.request("POST", "/api/v1/attempts", json={}), 400, "BAD_REQUEST")


# ---------------------------------------------------------------------------
# Enrolment validation
# ---------------------------------------------------------------------------


def test_learner_without_an_enrolment_is_rejected(api: ApiClient, seeded: dict) -> None:
    other = api.as_learner(OTHER_LEARNER_ID)
    error = assert_error(other.create_attempt(QUIZ_ID), 403, "LEARNER_NOT_ENROLLED")
    assert error["context"]["learnerId"] == OTHER_LEARNER_ID


@pytest.mark.parametrize(
    "status", [EnrolmentStatus.SUSPENDED, EnrolmentStatus.WITHDRAWN]
)
def test_inactive_enrolment_is_rejected(
    context: AppContext, api: ApiClient, status: EnrolmentStatus
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, enrolment_status=status)

    error = assert_error(api.create_attempt(QUIZ_ID), 403, "ENROLMENT_NOT_ACTIVE")
    assert error["context"]["enrolmentStatus"] == str(status)


def test_completed_enrolment_may_still_attempt(context: AppContext, api: ApiClient) -> None:
    # A learner who has finished the course can still re-attempt a quiz.
    with context.unit_of_work() as ctx:
        seed_world(ctx, enrolment_status=EnrolmentStatus.COMPLETED)

    assert_ok(api.create_attempt(QUIZ_ID), 201)


def test_failed_validation_persists_nothing(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, enrolment_status=EnrolmentStatus.SUSPENDED)

    assert_error(api.create_attempt(QUIZ_ID), 403, "ENROLMENT_NOT_ACTIVE")

    # No partial attempt, and no orphaned questions.
    with context.unit_of_work() as ctx:
        assert ctx.attempts_repo.count_for_learner_and_quiz(LEARNER_ID, QUIZ_ID) == 0
        assert ctx.session.query(AttemptQuestion).count() == 0


# ---------------------------------------------------------------------------
# Attempt limits
# ---------------------------------------------------------------------------


def _submit(api: ApiClient, attempt_id: str) -> None:
    assert_ok(api.submit(attempt_id, idempotency_key=f"key-{attempt_id}"))


def test_maximum_attempts_is_enforced(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"maxAttempts": 2})

    first = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    _submit(api, first["attemptId"])

    second = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    assert second["attemptNumber"] == 2
    _submit(api, second["attemptId"])

    error = assert_error(api.create_attempt(QUIZ_ID), 409, "MAX_ATTEMPTS_REACHED")
    assert error["context"] == {"attemptsUsed": 2, "maxAttempts": 2, "attemptsRemaining": 0}


def test_unlimited_attempts_when_max_is_null(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"maxAttempts": None})

    for expected_number in (1, 2, 3):
        attempt = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
        assert attempt["attemptNumber"] == expected_number
        _submit(api, attempt["attemptId"])


def test_remaining_attempts_are_reported_before_creation(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"maxAttempts": 3})

    report = assert_ok(api.eligibility(QUIZ_ID))["eligibility"]
    assert report["eligible"] is True
    assert report["attemptsUsed"] == 0
    assert report["attemptsRemaining"] == 3
    assert report["maxAttempts"] == 3
    assert report["enrolled"] is True
    assert report["openAttemptId"] is None

    attempt = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]

    # An in-progress attempt has consumed one of the allowance.
    during = assert_ok(api.eligibility(QUIZ_ID))["eligibility"]
    assert during["attemptsUsed"] == 1
    assert during["attemptsRemaining"] == 2
    assert during["openAttemptId"] == attempt["attemptId"]
    assert during["eligible"] is False
    assert [reason["code"] for reason in during["reasons"]] == ["ACTIVE_ATTEMPT_EXISTS"]

    _submit(api, attempt["attemptId"])

    after = assert_ok(api.eligibility(QUIZ_ID))["eligibility"]
    assert after["attemptsUsed"] == 1
    assert after["attemptsRemaining"] == 2
    assert after["eligible"] is True


def test_eligibility_explains_a_non_enrolled_learner(api: ApiClient, seeded: dict) -> None:
    report = assert_ok(api.as_learner(OTHER_LEARNER_ID).eligibility(QUIZ_ID))["eligibility"]
    assert report["eligible"] is False
    assert report["enrolled"] is False
    assert "LEARNER_NOT_ENROLLED" in [reason["code"] for reason in report["reasons"]]


def test_eligibility_reports_exhausted_attempts(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"maxAttempts": 1})

    attempt = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    _submit(api, attempt["attemptId"])

    report = assert_ok(api.eligibility(QUIZ_ID))["eligibility"]
    assert report["eligible"] is False
    assert report["attemptsRemaining"] == 0
    assert "MAX_ATTEMPTS_REACHED" in [reason["code"] for reason in report["reasons"]]


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_another_learner_cannot_read_the_attempt(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx)
        seed_enrolment(ctx, learner_id=OTHER_LEARNER_ID)

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    # 404 rather than 403: another learner's attempt must be indistinguishable from
    # one that does not exist.
    assert_error(api.as_learner(OTHER_LEARNER_ID).get_attempt(attempt_id), 404, "ATTEMPT_NOT_FOUND")


def test_unknown_attempt_returns_not_found(api: ApiClient, seeded: dict) -> None:
    assert_error(api.get_attempt("11111111-2222-3333-4444-555555555555"), 404, "ATTEMPT_NOT_FOUND")
