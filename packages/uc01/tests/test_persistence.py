"""Persistence: migrations, schema, repository parity, and the event seam."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from uc01.domain.enums import (
    DependencyName,
    LinkedResourceType,
    NaricLevelSource,
    SessionMode,
    SessionStatus,
)
from uc01.domain.models import LinkedResource, SessionEvent, SessionRecord
from uc01.persistence.db import Database
from uc01.persistence.memory_repository import InMemorySessionRepository
from uc01.persistence.migrate import main as migrate_main
from uc01.persistence.sqlite_repository import SqliteSessionRepository

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def record(session_id: str = "sess_1", user_id: str = "u_1") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        user_id=user_id,
        session_type=SessionMode.COURSE_LINKED,
        status=SessionStatus.DEGRADED,
        created_at=NOW,
        updated_at=NOW,
        naric_level=5,
        naric_level_source=NaricLevelSource.DEFAULT,
        explanation_level=5,
        linked_resource=LinkedResource(
            resource_type=LinkedResourceType.COURSE,
            resource_id="c1",
            label="Course One",
            secondary_id="l1",
            secondary_label="Lesson One",
        ),
        requested_mode=SessionMode.COURSE_LINKED,
        degraded_dependencies=(DependencyName.NARIC, DependencyName.PROFILE),
        diagnostics={"dependencies": {"naric": {"state": "unavailable"}}},
        greeting_variant="generic.course_linked",
        system_prompt_id="uc01.coaching.greeting",
        system_prompt_version="1.0.0",
    )


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #


def test_migrations_create_the_expected_schema(tmp_path):
    database = Database(str(tmp_path / "m.sqlite3"))
    try:
        applied = database.migrate()
        assert applied == ["001_init"]

        tables = {
            row["name"]
            for row in database.query("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"coaching_sessions", "session_events", "schema_migrations"} <= tables

        columns = {
            row["name"]
            for row in database.query("PRAGMA table_info(coaching_sessions)")
        }
        # The required UC-01 record fields must all exist.
        assert {
            "session_id",
            "user_id",
            "session_type",
            "linked_resource_type",
            "linked_resource_id",
            "naric_level",
            "created_at",
            "status",
            "naric_level_source",
        } <= columns
    finally:
        database.close()


def test_migrations_are_idempotent(tmp_path):
    path = str(tmp_path / "idem.sqlite3")
    first = Database(path)
    assert first.migrate() == ["001_init"]
    assert first.migrate() == []
    first.close()

    reopened = Database(path)
    assert reopened.migrate() == []
    assert reopened.applied_migrations() == ["001_init"]
    reopened.close()


def test_migration_cli_reports_status(tmp_path, capsys):
    path = str(tmp_path / "cli.sqlite3")
    assert migrate_main(["--path", path]) == 0
    assert "applied 001_init" in capsys.readouterr().out

    assert migrate_main(["--path", path]) == 0
    assert "already up to date" in capsys.readouterr().out

    assert migrate_main(["--path", path, "--status"]) == 0
    assert "001_init" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Repository behaviour (both implementations, same assertions)
# --------------------------------------------------------------------------- #


@pytest.fixture(params=["sqlite", "memory"])
def repo(request, tmp_path):
    if request.param == "memory":
        yield InMemorySessionRepository()
        return
    database = Database(str(tmp_path / "repo.sqlite3"))
    database.migrate()
    try:
        yield SqliteSessionRepository(database)
    finally:
        database.close()


def test_create_and_read_round_trip(repo):
    original = record()
    repo.create(original)
    loaded = repo.get("sess_1")
    assert loaded is not None
    assert loaded.session_id == original.session_id
    assert loaded.session_type is SessionMode.COURSE_LINKED
    assert loaded.status is SessionStatus.DEGRADED
    assert loaded.naric_level == 5
    assert loaded.naric_level_source is NaricLevelSource.DEFAULT
    assert loaded.linked_resource.secondary_label == "Lesson One"
    assert set(loaded.degraded_dependencies) == {DependencyName.NARIC, DependencyName.PROFILE}
    assert loaded.diagnostics["dependencies"]["naric"]["state"] == "unavailable"
    assert loaded.timestamp == NOW


def test_update_changes_status_and_keeps_identity(repo):
    stored = record()
    repo.create(stored)
    stored.status = SessionStatus.ACTIVE
    stored.failure_code = None
    repo.update(stored)
    assert repo.get("sess_1").status is SessionStatus.ACTIVE


def test_missing_session_returns_none(repo):
    assert repo.get("sess_missing") is None


def test_list_for_user_is_scoped_to_that_user(repo):
    repo.create(record("sess_a", "u_1"))
    repo.create(record("sess_b", "u_2"))
    assert {r.session_id for r in repo.list_for_user("u_1")} == {"sess_a"}
    assert {r.session_id for r in repo.list_for_user("u_2")} == {"sess_b"}
    assert repo.list_for_user("u_nobody") == ()


def test_events_are_append_only_and_ordered(repo):
    repo.create(record())
    for index, event_type in enumerate(["a", "b", "c"]):
        repo.append_event(
            SessionEvent(
                session_id="sess_1",
                event_type=event_type,
                occurred_at=NOW,
                payload={"index": index},
            )
        )
    events = repo.list_events("sess_1")
    assert [event.event_type for event in events] == ["a", "b", "c"]
    assert [event.payload["index"] for event in events] == [0, 1, 2]
    assert repo.list_events("sess_other") == ()


def test_event_payload_supports_future_use_case_fields(repo):
    """The seam UC-07 / UC-10 would use, without UC-01 implementing their behaviour."""
    repo.create(record())
    repo.append_event(
        SessionEvent(
            session_id="sess_1",
            event_type="example.future_event",
            occurred_at=NOW,
            payload={
                "question": "What is consideration?",
                "topic_tag": "contract-law",
                "explain_differently_count": 2,
                "rating": 5,
            },
        )
    )
    stored = repo.list_events("sess_1")[-1]
    assert stored.payload["topic_tag"] == "contract-law"
    assert stored.payload["rating"] == 5


def test_repositories_are_interchangeable_for_the_service(repo, build_service):
    """Same service code, either store."""
    from uc01.application.dto import OpenSessionCommand
    from uc01.domain.models import UserContext

    service = build_service(repository=repo)
    result = service.open_session(
        UserContext(user_id="u_swap"), OpenSessionCommand(mode=SessionMode.FREE_FORM)
    )
    assert repo.get(result.record.session_id) is not None


def test_sqlite_enforces_the_event_foreign_key(tmp_path):
    database = Database(str(tmp_path / "fk.sqlite3"))
    database.migrate()
    repo = SqliteSessionRepository(database)
    try:
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            repo.append_event(
                SessionEvent(
                    session_id="sess_orphan",
                    event_type="x",
                    occurred_at=NOW,
                    payload={},
                )
            )
    finally:
        database.close()
