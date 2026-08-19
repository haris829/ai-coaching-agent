"""Alembic environment.

Reads the database URL from ``app.core.config.settings`` rather than alembic.ini, so switching
to the company database is a single configuration change.

An explicit URL still wins. Anything that sets ``sqlalchemy.url`` on the Alembic config — an
operator running a one-off migration against a specific database, or the drift test building a
scratch schema — is deliberately naming a target, and must not be silently redirected at whatever
``settings`` happens to hold.

``render_as_batch=True`` is required for SQLite, which cannot ALTER most constraints in place;
Alembic emulates it by rebuilding the table. It is harmless on other backends.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db.metadata import target_metadata  # noqa: E402  (registers every module's tables)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

#: The URL these migrations will run against.
database_url = config.get_main_option("sqlalchemy.url") or settings.database_url
config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
