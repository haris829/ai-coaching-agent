"""Declarative base and portable column helpers.

PORTABILITY
-----------
The company database is not chosen yet, so every model sticks to SQLAlchemy types that map
cleanly onto SQLite, PostgreSQL, MySQL and SQL Server:

* identifiers are ``String(36)`` UUID4 hex strings — no database-specific UUID type;
* enum-like columns are ``String`` validated by the authoritative domain layer, never a
  native ``ENUM``, so no migration is needed to add a new question type;
* frozen structures are ``Text`` holding JSON, not ``JSONB``;
* every constraint and index is explicitly named through ``NAMING_CONVENTION`` so Alembic can
  drop/alter them by name on any backend (SQLite in particular cannot alter unnamed ones);
* timestamp columns default to :func:`app.core.time.utcnow`, the system's single clock, and map
  to :class:`app.db.types.UtcDateTime`, which refuses naive datetimes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.time import utcnow
from app.db.types import UtcDateTime

# Deterministic constraint names — required for Alembic autogenerate + SQLite batch mode.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# All UC-02 tables are prefixed `qb_` so this schema merges into the larger Courses Quiz
# Agent database without colliding with another module's tables.
TABLE_PREFIX = "qb_"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    #: Defaults applied when a column declares no explicit type.
    #:
    #: ``UtcDateTime`` **rejects naive datetimes on write** and reattaches UTC on read, so a
    #: timezone can never be silently assumed — SQLite otherwise hands back naive values and that
    #: is exactly how timing bugs start. Columns that pass an explicit type are unaffected.
    type_annotation_map = {
        datetime: UtcDateTime,
        dict[str, Any]: JSON,
    }


def new_id() -> str:
    """Application-generated primary key. Portable across every candidate database."""
    return uuid.uuid4().hex


def id_column() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_id)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
