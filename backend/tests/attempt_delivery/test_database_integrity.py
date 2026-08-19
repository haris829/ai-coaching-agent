"""Database integrity.

These tests go under the service layer and attack the schema directly, because the
constraints are the last line of defence: if application logic ever regresses, these are
what stop a duplicate submission, an orphaned answer or an incoherent lifecycle from
being persisted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import (
    AttemptStatus,
    SubmissionReason,
    SubmissionState,
)
from app.modules.attempt_delivery.ids import new_id
from app.modules.attempt_delivery.models import (
    AttemptAnswer,
    AttemptQuestion,
    AttemptQuestionFlag,
    AttemptSubmission,
    QuizAttempt,
)
from tests.support.client import ApiClient, assert_ok
from tests.support.fixtures import LEARNER_ID, QUIZ_ID, answer_for, seed_world


def _attempt(context: AppContext, api: ApiClient, count: int = 3) -> str:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": count})
    return assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]


# ---------------------------------------------------------------------------
# Foreign keys and cascades
# ---------------------------------------------------------------------------


def test_foreign_keys_are_enforced(context: AppContext) -> None:
    # SQLite disables foreign keys by default; the engine turns them on per connection.
    with context.unit_of_work() as ctx:
        assert ctx.session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_an_answer_cannot_reference_a_missing_attempt(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        question = ctx.attempt_questions_repo.list_for_attempt(attempt_id)[0]
        ctx.session.add(
            AttemptAnswer(
                id=new_id(),
                attempt_id="attempt-that-does-not-exist",
                attempt_question_id=question.id,
                question_id=question.question_id,
                answered=False,
                complete=False,
                response=None,
                response_hash=None,
                revision=1,
                source="MANUAL",
                first_saved_at=ctx.timing.now(),
                saved_at=ctx.timing.now(),
            )
        )


def test_deleting_an_attempt_cascades(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)
    questions = assert_ok(api.questions(attempt_id))["questions"]
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    assert_ok(api.set_flag(attempt_id, questions[1]["questionId"], True))
    assert_ok(api.submit(attempt_id, idempotency_key="k1"))

    with context.unit_of_work() as ctx:
        attempt = ctx.attempts_repo.get(attempt_id)
        assert attempt is not None
        ctx.session.delete(attempt)

    # No orphaned children survive.
    with context.unit_of_work() as ctx:
        assert ctx.session.query(AttemptQuestion).count() == 0
        assert ctx.session.query(AttemptAnswer).count() == 0
        assert ctx.session.query(AttemptQuestionFlag).count() == 0
        assert ctx.session.query(AttemptSubmission).count() == 0


# ---------------------------------------------------------------------------
# Unique constraints
# ---------------------------------------------------------------------------


def test_only_one_open_attempt_per_learner_and_quiz(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)

    # Bypass the service entirely: the database itself must refuse a second open attempt.
    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        existing = ctx.attempts_repo.get(attempt_id)
        assert existing is not None
        ctx.session.add(
            QuizAttempt(
                id=new_id(),
                learner_id=existing.learner_id,
                course_id=existing.course_id,
                quiz_id=existing.quiz_id,
                configuration_version_id=existing.configuration_version_id,
                configuration_version_number=existing.configuration_version_number,
                configuration_snapshot=existing.configuration_snapshot,
                attempt_number=99,
                status=str(AttemptStatus.ACTIVE),
                question_presentation=existing.question_presentation,
                selection_seed="seed",
                total_questions=0,
                current_position=1,
                time_limit_seconds=None,
                started_at=existing.started_at,
                expires_at=None,
                last_activity_at=existing.started_at,
                created_at=existing.started_at,
                updated_at=existing.started_at,
            )
        )


def test_attempt_numbers_are_unique_per_learner_and_quiz(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id = _attempt(context, api)
    assert_ok(api.submit(attempt_id, idempotency_key="k1"))

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        existing = ctx.attempts_repo.get(attempt_id)
        assert existing is not None
        # A second attempt reusing attempt_number 1.
        ctx.session.add(
            QuizAttempt(
                id=new_id(),
                learner_id=existing.learner_id,
                course_id=existing.course_id,
                quiz_id=existing.quiz_id,
                configuration_version_id=existing.configuration_version_id,
                configuration_version_number=existing.configuration_version_number,
                configuration_snapshot=existing.configuration_snapshot,
                attempt_number=1,
                status=str(AttemptStatus.ACTIVE),
                question_presentation=existing.question_presentation,
                selection_seed="seed",
                total_questions=0,
                current_position=1,
                time_limit_seconds=None,
                started_at=existing.started_at,
                expires_at=None,
                last_activity_at=existing.started_at,
                created_at=existing.started_at,
                updated_at=existing.started_at,
            )
        )


def test_at_most_one_successful_submission_per_attempt(
    context: AppContext, api: ApiClient
) -> None:
    attempt_id = _attempt(context, api)
    assert_ok(api.submit(attempt_id, idempotency_key="k1"))

    # The partial unique index is the hard guarantee behind submission idempotency.
    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        now = ctx.timing.now()
        ctx.session.add(
            AttemptSubmission(
                id=new_id(),
                attempt_id=attempt_id,
                idempotency_key="a-different-key",
                request_fingerprint="whatever",
                state=str(SubmissionState.SUBMITTED),
                submission_reason=str(SubmissionReason.LEARNER_CONFIRMED),
                attempt_count=1,
                answered_count=0,
                total_questions=3,
                requested_at=now,
                last_attempted_at=now,
                completed_at=now,
            )
        )


def test_idempotency_key_is_unique_per_attempt(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)
    assert_ok(api.submit(attempt_id, idempotency_key="k1"))

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        now = ctx.timing.now()
        ctx.session.add(
            AttemptSubmission(
                id=new_id(),
                attempt_id=attempt_id,
                idempotency_key="k1",
                request_fingerprint="whatever",
                state=str(SubmissionState.PENDING),
                submission_reason=str(SubmissionReason.LEARNER_CONFIRMED),
                attempt_count=1,
                answered_count=0,
                total_questions=3,
                requested_at=now,
                last_attempted_at=now,
            )
        )


def test_a_question_cannot_occupy_two_positions(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        existing = ctx.attempt_questions_repo.list_for_attempt(attempt_id)[0]
        ctx.session.add(
            AttemptQuestion(
                id=new_id(),
                attempt_id=attempt_id,
                question_id=existing.question_id,
                question_version=1,
                question_type=existing.question_type,
                position=99,
                points=1.0,
                question_snapshot=existing.question_snapshot,
                created_at=existing.created_at,
            )
        )


def test_one_answer_row_per_delivered_question(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)
    questions = assert_ok(api.questions(attempt_id))["questions"]
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        question = ctx.attempt_questions_repo.find_by_question_id(
            attempt_id, questions[0]["questionId"]
        )
        assert question is not None
        now = ctx.timing.now()
        ctx.session.add(
            AttemptAnswer(
                id=new_id(),
                attempt_id=attempt_id,
                attempt_question_id=question.id,
                question_id=question.question_id,
                answered=False,
                complete=False,
                response=None,
                response_hash=None,
                revision=1,
                source="MANUAL",
                first_saved_at=now,
                saved_at=now,
            )
        )


# ---------------------------------------------------------------------------
# Check constraints
# ---------------------------------------------------------------------------


def test_an_incoherent_lifecycle_is_rejected(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)

    # ACTIVE with a submission reason is not a representable state.
    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        ctx.session.execute(
            text(
                "UPDATE qd_attempts SET submission_reason = 'LEARNER_CONFIRMED' WHERE id = :id"
            ),
            {"id": attempt_id},
        )


def test_submitted_without_a_timestamp_is_rejected(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        ctx.session.execute(
            text("UPDATE qd_attempts SET status = 'SUBMITTED' WHERE id = :id"),
            {"id": attempt_id},
        )


def test_answered_without_a_response_is_rejected(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)
    questions = assert_ok(api.questions(attempt_id))["questions"]
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        ctx.session.execute(
            text("UPDATE qd_attempt_answers SET response = NULL WHERE attempt_id = :id"),
            {"id": attempt_id},
        )


def test_complete_without_answered_is_rejected(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)
    questions = assert_ok(api.questions(attempt_id))["questions"]
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        ctx.session.execute(
            text(
                "UPDATE qd_attempt_answers SET answered = 0, response = NULL, complete = 1 "
                "WHERE attempt_id = :id"
            ),
            {"id": attempt_id},
        )


def test_a_timed_attempt_must_have_an_expiry(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        ctx.session.execute(
            text("UPDATE qd_attempts SET expires_at = NULL WHERE id = :id"),
            {"id": attempt_id},
        )


def test_a_flag_must_carry_an_instant(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)
    questions = assert_ok(api.questions(attempt_id))["questions"]
    assert_ok(api.set_flag(attempt_id, questions[0]["questionId"], True))

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        ctx.session.execute(
            text("UPDATE qd_attempt_question_flags SET flagged_at = NULL WHERE attempt_id = :id"),
            {"id": attempt_id},
        )


def test_unknown_status_values_are_rejected(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)

    with pytest.raises(IntegrityError), context.unit_of_work() as ctx:
        ctx.session.execute(
            text("UPDATE qd_attempts SET status = 'PAUSED' WHERE id = :id"),
            {"id": attempt_id},
        )


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


def test_the_expected_indexes_exist(context: AppContext) -> None:
    inspector = inspect(context.engine)
    names: set[str] = set()
    for table in inspector.get_table_names():
        names.update(index["name"] or "" for index in inspector.get_indexes(table))

    # The indexes the hot paths and the integrity guarantees depend on.
    for expected in (
        "ux_attempt_single_open",
        "ux_submission_single_success",
        "ix_attempt_learner_quiz",
        "ix_attempt_expiry",
        "ix_attempt_config_version",
        "ix_attempt_question_question",
        "ix_flag_attempt_flagged",
        "ix_submission_state",
        "ix_answer_revision_attempt",
    ):
        assert expected in names, f"missing index {expected}"


def test_partial_unique_indexes_allow_multiple_closed_rows(
    context: AppContext, api: ApiClient
) -> None:
    # The single-open-attempt index must not prevent a learner accumulating a history of
    # submitted attempts.
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 2, "maxAttempts": None})

    for index in range(3):
        attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
        assert_ok(api.submit(attempt_id, idempotency_key=f"k{index}"))

    with context.unit_of_work() as ctx:
        assert ctx.attempts_repo.count_for_learner_and_quiz(LEARNER_ID, QUIZ_ID) == 3


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def test_stored_timestamps_are_timezone_aware_utc(context: AppContext, api: ApiClient) -> None:
    attempt_id = _attempt(context, api)

    with context.unit_of_work() as ctx:
        attempt = ctx.attempts_repo.get(attempt_id)
        assert attempt is not None
        # A naive datetime coming back out of the database is exactly the bug the
        # UtcDateTime decorator exists to prevent.
        for value in (
            attempt.started_at,
            attempt.expires_at,
            attempt.created_at,
            attempt.updated_at,
            attempt.last_activity_at,
        ):
            assert value is not None
            assert value.tzinfo is not None
            assert value.utcoffset().total_seconds() == 0


def test_naive_timestamps_are_refused_at_the_boundary(context: AppContext, api: ApiClient) -> None:
    from datetime import datetime

    attempt_id = _attempt(context, api)

    with pytest.raises(Exception) as caught, context.unit_of_work() as ctx:
        ctx.attempts_repo.touch_activity(attempt_id, datetime(2026, 3, 1, 12, 0, 0))
    assert "timezone-aware" in str(caught.value)
