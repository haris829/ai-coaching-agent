"""SQLite implementation of :class:`uc01.contracts.repository.SessionRepository`.

This is a *standalone development store*, not a claim about the final company database.
All mapping between domain objects and SQL rows lives here, so replacing the store means
writing one new class.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any

from ..domain.enums import (
    DependencyFailurePolicy,
    DependencyName,
    LinkedResourceType,
    NaricLevelSource,
    SessionMode,
    SessionStatus,
)
from ..domain.models import LinkedResource, SessionEvent, SessionRecord
from .db import Database

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO coaching_sessions (
    session_id, user_id, session_type, status, requested_mode, downgraded_from,
    linked_resource_type, linked_resource_id, linked_resource_label,
    linked_resource_secondary_id, linked_resource_secondary_label,
    naric_level, naric_level_source, explanation_level,
    degraded_dependencies, failure_code, diagnostics_json,
    greeting_variant, system_prompt_id, system_prompt_version,
    dependency_failure_policy, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPDATE_SQL = """
UPDATE coaching_sessions SET
    session_type = ?, status = ?, requested_mode = ?, downgraded_from = ?,
    linked_resource_type = ?, linked_resource_id = ?, linked_resource_label = ?,
    linked_resource_secondary_id = ?, linked_resource_secondary_label = ?,
    naric_level = ?, naric_level_source = ?, explanation_level = ?,
    degraded_dependencies = ?, failure_code = ?, diagnostics_json = ?,
    greeting_variant = ?, system_prompt_id = ?, system_prompt_version = ?,
    dependency_failure_policy = ?, updated_at = ?
WHERE session_id = ?
"""


class SqliteSessionRepository:
    """Durable-enough session store for standalone development."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- writes ------------------------------------------------------------- #

    def create(self, record: SessionRecord) -> SessionRecord:
        try:
            self._db.execute(_INSERT_SQL, self._insert_params(record))
        except sqlite3.Error as exc:
            # Logged with technical detail; the caller turns this into a safe response.
            logger.error(
                "persistence.session.create_failed",
                extra={"uc01": {"session_id": record.session_id, "error": str(exc)}},
            )
            raise
        return record

    def update(self, record: SessionRecord) -> SessionRecord:
        try:
            cursor = self._db.execute(_UPDATE_SQL, self._update_params(record))
        except sqlite3.Error as exc:
            logger.error(
                "persistence.session.update_failed",
                extra={"uc01": {"session_id": record.session_id, "error": str(exc)}},
            )
            raise
        if cursor.rowcount == 0:
            logger.warning(
                "persistence.session.update_missing",
                extra={"uc01": {"session_id": record.session_id}},
            )
        return record

    def append_event(self, event: SessionEvent) -> SessionEvent:
        cursor = self._db.execute(
            """
            INSERT INTO session_events (session_id, event_type, occurred_at, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                event.session_id,
                event.event_type,
                event.occurred_at.isoformat(),
                json.dumps(_jsonable(event.payload), sort_keys=True),
            ),
        )
        return SessionEvent(
            session_id=event.session_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            payload=event.payload,
            event_id=cursor.lastrowid,
        )

    # -- reads -------------------------------------------------------------- #

    def get(self, session_id: str) -> SessionRecord | None:
        row = self._db.query_one(
            "SELECT * FROM coaching_sessions WHERE session_id = ?", (session_id,)
        )
        return self._to_record(row) if row else None

    def list_for_user(self, user_id: str, limit: int = 50) -> Sequence[SessionRecord]:
        rows = self._db.query(
            """
            SELECT * FROM coaching_sessions
            WHERE user_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        return tuple(self._to_record(row) for row in rows)

    def list_events(self, session_id: str) -> Sequence[SessionEvent]:
        rows = self._db.query(
            "SELECT * FROM session_events WHERE session_id = ? ORDER BY event_id",
            (session_id,),
        )
        return tuple(
            SessionEvent(
                session_id=row["session_id"],
                event_type=row["event_type"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                payload=json.loads(row["payload_json"]),
                event_id=row["event_id"],
            )
            for row in rows
        )

    # -- mapping ------------------------------------------------------------ #

    @staticmethod
    def _linked_columns(record: SessionRecord) -> tuple[Any, ...]:
        linked = record.linked_resource
        return (
            linked.resource_type.value if linked else None,
            linked.resource_id if linked else None,
            linked.label if linked else None,
            linked.secondary_id if linked else None,
            linked.secondary_label if linked else None,
        )

    def _insert_params(self, record: SessionRecord) -> tuple[Any, ...]:
        return (
            record.session_id,
            record.user_id,
            record.session_type.value,
            record.status.value,
            record.requested_mode.value if record.requested_mode else None,
            record.downgraded_from.value if record.downgraded_from else None,
            *self._linked_columns(record),
            record.naric_level,
            record.naric_level_source.value,
            record.explanation_level,
            json.dumps([dep.value for dep in record.degraded_dependencies]),
            record.failure_code,
            json.dumps(_jsonable(record.diagnostics), sort_keys=True),
            record.greeting_variant,
            record.system_prompt_id,
            record.system_prompt_version,
            record.dependency_failure_policy.value,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        )

    def _update_params(self, record: SessionRecord) -> tuple[Any, ...]:
        return (
            record.session_type.value,
            record.status.value,
            record.requested_mode.value if record.requested_mode else None,
            record.downgraded_from.value if record.downgraded_from else None,
            *self._linked_columns(record),
            record.naric_level,
            record.naric_level_source.value,
            record.explanation_level,
            json.dumps([dep.value for dep in record.degraded_dependencies]),
            record.failure_code,
            json.dumps(_jsonable(record.diagnostics), sort_keys=True),
            record.greeting_variant,
            record.system_prompt_id,
            record.system_prompt_version,
            record.dependency_failure_policy.value,
            record.updated_at.isoformat(),
            record.session_id,
        )

    @staticmethod
    def _to_record(row: sqlite3.Row) -> SessionRecord:
        linked: LinkedResource | None = None
        if row["linked_resource_type"] and row["linked_resource_id"]:
            linked = LinkedResource(
                resource_type=LinkedResourceType(row["linked_resource_type"]),
                resource_id=row["linked_resource_id"],
                label=row["linked_resource_label"] or row["linked_resource_id"],
                secondary_id=row["linked_resource_secondary_id"],
                secondary_label=row["linked_resource_secondary_label"],
            )
        return SessionRecord(
            session_id=row["session_id"],
            user_id=row["user_id"],
            session_type=SessionMode(row["session_type"]),
            status=SessionStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            naric_level=row["naric_level"],
            naric_level_source=NaricLevelSource(row["naric_level_source"]),
            explanation_level=row["explanation_level"],
            linked_resource=linked,
            requested_mode=SessionMode(row["requested_mode"]) if row["requested_mode"] else None,
            downgraded_from=(
                SessionMode(row["downgraded_from"]) if row["downgraded_from"] else None
            ),
            degraded_dependencies=tuple(
                DependencyName(name) for name in json.loads(row["degraded_dependencies"])
            ),
            failure_code=row["failure_code"],
            diagnostics=json.loads(row["diagnostics_json"]),
            greeting_variant=row["greeting_variant"],
            system_prompt_id=row["system_prompt_id"],
            system_prompt_version=row["system_prompt_version"],
            dependency_failure_policy=DependencyFailurePolicy(
                row["dependency_failure_policy"]
            ),
        )


def _jsonable(value: Any) -> Any:
    """Make diagnostics/payloads JSON-safe without losing information."""
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = ["SqliteSessionRepository"]
