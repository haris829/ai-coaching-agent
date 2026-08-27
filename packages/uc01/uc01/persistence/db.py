"""SQLite connection management and the migration runner.

Standalone development persistence. Limitations are documented in
``docs/PERSISTENCE.md``; the important ones are single-writer and file-local.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


class Database:
    """A single SQLite connection guarded by a lock.

    One connection is used with ``check_same_thread=False`` because the ASGI server and
    the test client both run handlers on worker threads. The lock keeps writes
    serialised, which is the correct trade-off for a development store.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            path if path == ":memory:" else str(Path(path).expanduser()),
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")

    # -- lifecycle ---------------------------------------------------------- #

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # -- access ------------------------------------------------------------- #

    def execute(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._connection.execute(sql, tuple(params))
            self._connection.commit()
            return cursor

    def query(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- migrations --------------------------------------------------------- #

    def migrate(self) -> list[str]:
        """Apply every unapplied ``.sql`` file in ``migrations/`` in filename order.

        Returns the versions applied by this call. Idempotent.
        """
        applied: list[str] = []
        with self._lock:
            self._connection.executescript(_BOOTSTRAP_SQL)
            known = {
                row["version"]
                for row in self._connection.execute("SELECT version FROM schema_migrations")
            }
            for migration in _migration_files():
                version = migration.stem
                if version in known:
                    continue
                logger.info("persistence.migration.apply", extra={"uc01": {"version": version}})
                self._connection.executescript(migration.read_text(encoding="utf-8"))
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(UTC).isoformat()),
                )
                applied.append(version)
            self._connection.commit()
        return applied

    def applied_migrations(self) -> list[str]:
        rows = self.query("SELECT version FROM schema_migrations ORDER BY version")
        return [row["version"] for row in rows]


def _migration_files() -> Iterator[Path]:
    return iter(sorted(MIGRATIONS_DIR.glob("*.sql")))


__all__ = ["Database", "MIGRATIONS_DIR"]
