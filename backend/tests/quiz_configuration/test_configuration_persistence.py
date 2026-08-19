"""Configuration persistence must be atomic — no partial saves, ever.

The failure injection here patches the repository classes rather than the service, so the service
runs its real transaction handling and the rollback is genuine.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.modules.quiz_configuration.repositories import (
    SqlAlchemyConfigurationVersionRepository,
    SqlAlchemyQuizRepository,
)
from tests.harness import Ctx, valid_configuration

VERSIONS = SqlAlchemyConfigurationVersionRepository
QUIZZES = SqlAlchemyQuizRepository


def _explode(message: str):
    def boom(*_args, **_kwargs):
        raise OperationalError("INSERT ...", {}, Exception(message))

    return boom


def test_persists_the_complete_configuration_snapshot(ctx: Ctx) -> None:
    response = ctx.save_configuration(
        valid_configuration(
            questionCount=12,
            timeLimitMinutes=45,
            passMark=75,
            maxAttempts=4,
            deliveryMode="exam",
            randomiseQuestions=True,
            questionTypes=[
                {"type": "SINGLE_CHOICE", "quota": 8},
                {"type": "TRUE_FALSE", "quota": 4},
            ],
        )
    )
    assert response.status_code == 201, response.text

    # Read back from the database rather than trusting the response body.
    with ctx.session() as session:
        quizzes = QUIZZES(session)
        versions = VERSIONS(session)
        quiz = quizzes.get(ctx.quiz_id)
        assert quiz is not None
        stored = versions.get_active(quiz)
        assert stored is not None
        assert stored.version_number == 1
        assert stored.question_count == 12
        assert stored.time_limit_minutes == 45
        assert stored.pass_mark == 75
        assert stored.max_attempts == 4
        assert stored.delivery_mode == "exam"
        assert bool(stored.randomise_questions) is True
        assert stored.created_by_user_id == ctx.admin_id
        assert stored.created_by == "admin@test.local"
        assert [
            (entry.question_type, entry.question_quota) for entry in stored.question_types
        ] == [("SINGLE_CHOICE", 8), ("TRUE_FALSE", 4)]


def test_rolls_back_when_writing_question_types_fails(ctx: Ctx, monkeypatch) -> None:
    monkeypatch.setattr(VERSIONS, "insert_question_types", _explode("disk I/O error"))

    response = ctx.save_configuration(valid_configuration())

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "PERSISTENCE_FAILED"
    assert body["error"]["retryable"] is True
    # The internal database message stays server-side.
    assert "disk I/O error" not in response.text

    # Nothing partial: no version row, no question-type rows, quiz still unconfigured.
    assert ctx.version_count() == 0
    assert ctx.question_type_row_count() == 0
    assert ctx.active_version_id() is None


def test_rolls_back_when_writing_the_topic_scope_fails(ctx: Ctx, monkeypatch) -> None:
    from tests import bank

    with ctx.session() as session:
        topic_id = bank.topic_named(session, "Networking").id

    monkeypatch.setattr(VERSIONS, "insert_topics", _explode("database is locked"))

    response = ctx.save_configuration(valid_configuration(topicIds=[topic_id]))

    assert response.status_code == 503
    assert ctx.version_count() == 0
    assert ctx.question_type_row_count() == 0
    assert ctx.active_version_id() is None


def test_rolls_back_activation_failure_and_keeps_the_previous_version_active(
    ctx: Ctx, monkeypatch
) -> None:
    first = ctx.save_configuration(valid_configuration())
    assert first.status_code == 201
    first_version_id = first.json()["configuration"]["id"]

    monkeypatch.setattr(
        QUIZZES, "set_active_configuration_version", _explode("database is locked")
    )

    second = ctx.save_configuration(valid_configuration(passMark=80))
    assert second.status_code == 503

    monkeypatch.undo()

    # Version 2 was never created, and version 1 is still active and unchanged.
    assert ctx.version_count() == 1
    assert ctx.active_version_id() == first_version_id
    assert ctx.get_configuration().json()["configuration"]["passMark"] == 60


def test_administrator_can_retry_after_a_recoverable_failure(ctx: Ctx, monkeypatch) -> None:
    calls = {"count": 0}
    original = VERSIONS.insert_question_types

    def flaky(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("INSERT ...", {}, Exception("database is locked"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(VERSIONS, "insert_question_types", flaky)

    failed = ctx.save_configuration(valid_configuration())
    assert failed.status_code == 503
    assert ctx.version_count() == 0

    retried = ctx.save_configuration(valid_configuration())
    assert retried.status_code == 201
    # The retry produces version 1 — the failed save did not consume a version number.
    assert retried.json()["configuration"]["versionNumber"] == 1
    assert ctx.version_count() == 1


def test_no_version_is_created_when_validation_fails(ctx: Ctx, monkeypatch) -> None:
    called = {"value": False}

    def spy(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("must not be reached")

    monkeypatch.setattr(VERSIONS, "insert", spy)

    response = ctx.save_configuration(valid_configuration(passMark=0))
    assert response.status_code == 422
    assert called["value"] is False
    assert ctx.version_count() == 0


def test_no_version_is_created_when_the_bank_is_insufficient(ctx: Ctx, monkeypatch) -> None:
    called = {"value": False}

    def spy(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("must not be reached")

    monkeypatch.setattr(VERSIONS, "insert", spy)

    response = ctx.save_configuration(
        valid_configuration(
            questionCount=100, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 100}]
        )
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "QUESTION_BANK_INSUFFICIENT"
    assert called["value"] is False
    assert ctx.version_count() == 0


def test_a_failed_save_leaves_the_connection_usable(ctx: Ctx, monkeypatch) -> None:
    """A rollback must not poison the session for the next request."""
    monkeypatch.setattr(VERSIONS, "insert_question_types", _explode("transient"))
    assert ctx.save_configuration(valid_configuration()).status_code == 503
    monkeypatch.undo()

    assert ctx.get_configuration().status_code == 200
    assert ctx.save_configuration(valid_configuration()).status_code == 201


def test_database_check_constraints_reject_out_of_range_values(ctx: Ctx) -> None:
    """Even a direct write cannot store an invalid pass mark or attempt count."""
    template = (
        "INSERT INTO qc_configuration_versions "
        "(quiz_id, version_number, question_count, pass_mark, randomise_questions, "
        " max_attempts, delivery_mode, settings_fingerprint, created_by_user_id, created_at) "
        "VALUES (:quiz, 99, 5, :pass_mark, 0, :max_attempts, 'practice', 'fp', :user, "
        " '2026-01-01 00:00:00')"
    )
    for pass_mark, max_attempts in [(0, 1), (101, 1), (50, 0), (50, 51)]:
        with pytest.raises(Exception, match="CHECK constraint"):
            ctx.execute(
                template,
                quiz=ctx.quiz_id,
                pass_mark=pass_mark,
                max_attempts=max_attempts,
                user=ctx.admin_id,
            )


def test_database_rejects_an_unknown_question_type_on_a_version(ctx: Ctx) -> None:
    """The five supported types are enforced by a CHECK constraint too."""
    version_id = ctx.save_configuration(valid_configuration()).json()["configuration"]["id"]

    with pytest.raises(Exception, match="CHECK constraint"):
        ctx.execute(
            "INSERT INTO qc_configuration_version_question_types "
            "(configuration_version_id, question_type, question_quota, position) "
            "VALUES (:version, 'ESSAY', NULL, 9)",
            version=version_id,
        )
