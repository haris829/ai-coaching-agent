"""Engine and session management.

The engine is created from ``settings.database_url`` alone, so pointing the application at the
company database later is a configuration change, not a code change.

Two access styles, both supported on purpose:

* the module-level :data:`engine` / :data:`SessionLocal` / :func:`get_db`, used by the FastAPI
  dependency graph;
* :func:`create_db_engine` / :func:`create_session_factory`, used where an **isolated** engine is
  wanted — UC-03's composition root and the tests that drive time-sensitive behaviour against their
  own database.

SQLite specifics are confined to :func:`_configure_sqlite`. Three of them are load-bearing rather
than boilerplate:

* **Foreign keys are off by default, per connection.** Without the pragma every ``RESTRICT`` and
  ``CASCADE`` in the schema would be decorative — and the historical-integrity guarantees depend on
  them.
* **``busy_timeout``.** A concurrent writer waits its turn rather than failing instantly.
* **WAL** on a file database, for better concurrent read/write behaviour.

A server database handles all of these itself, which is why each is behind a dialect check.

On ``BEGIN IMMEDIATE``
---------------------
UC-03 originally took SQLite's write lock at ``BEGIN`` so that a write-write race failed fast rather
than half-way through a transaction that had already made decisions. That is **deliberately not done
here**: with a shared engine, a pooled connection and FastAPI's threadpool it conflicts with
pysqlite's own transaction handling ("cannot start a transaction within a transaction"), and it is
defence in depth rather than a guarantee. Every invariant it was protecting is enforced by the
schema instead, and those hold on any backend and under any pooling:

* one open attempt per learner+quiz — ``ux_attempt_single_open`` (partial unique index)
* at most one successful submission — ``ux_submission_single_success``
* idempotent retries — ``ux_submission_idempotency``
* gapless configuration versions — ``UNIQUE (quiz_id, version_number)``

A loser in a race therefore gets a clean constraint violation, which the services translate into a
``409``, rather than a lock error.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_memory_sqlite(url: str) -> bool:
    return _is_sqlite(url) and (":memory:" in url or "mode=memory" in url)


def create_db_engine(database_url: str | None = None, *, echo: bool | None = None) -> Engine:
    """Build an engine with the pragmas and locking behaviour this schema relies on."""
    url = database_url or settings.database_url
    kwargs: dict[str, Any] = {
        "echo": settings.database_echo if echo is None else echo,
        "future": True,
    }

    if _is_sqlite(url):
        # FastAPI serves requests from a thread pool, so a connection may move threads.
        kwargs["connect_args"] = {"check_same_thread": False}
        if _is_memory_sqlite(url):
            # An in-memory database lives inside its connection, so the whole engine must share
            # one; otherwise each session would see an empty schema.
            kwargs["poolclass"] = StaticPool
        else:
            path = url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    else:
        # Sensible defaults for a real server-based database.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800

    built = create_engine(url, **kwargs)
    if _is_sqlite(url):
        _configure_sqlite(built, memory=_is_memory_sqlite(url))
    return built


def _configure_sqlite(target: Engine, *, memory: bool) -> None:
    @event.listens_for(target, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        if not isinstance(dbapi_connection, sqlite3.Connection):  # pragma: no cover
            return
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 5000")
            if not memory:
                # WAL improves concurrent read/write behaviour for a file database; it does not
                # apply to an in-memory one.
                cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
        finally:
            cursor.close()


def create_session_factory(target: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=target, autocommit=False, autoflush=False, expire_on_commit=False, future=True
    )


def create_schema(target: Engine) -> None:
    """Create every table, from every module, directly from the models.

    Used by tests and the local bootstrap. Deployments use the Alembic migrations, and a test
    asserts the two agree so they cannot drift.
    """
    # Imported here so this module stays importable before the model packages are loaded.
    from app.db.metadata import target_metadata

    target_metadata.create_all(target)


engine: Engine = create_db_engine()

SessionLocal = create_session_factory(engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session.

    The route/service layer owns commits; this only guarantees rollback-on-error and close.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope(session_factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Transactional scope for scripts, seeds and tests."""
    session = (session_factory or SessionLocal)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
