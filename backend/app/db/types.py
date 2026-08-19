"""Custom SQLAlchemy column types, shared by every module."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator

from app.core.time import ensure_utc


class UtcDateTime(TypeDecorator[datetime]):
    """A timestamp column that is always timezone-aware UTC.

    SQLite has no native timestamp type and SQLAlchemy's plain ``DateTime`` hands
    back *naive* datetimes from it, which is precisely how timing bugs creep in.
    This decorator closes that gap at the boundary:

    * on write, a naive datetime is rejected outright rather than assumed to be
      UTC or local time, and an aware one is normalised to UTC;
    * on read, UTC is reattached, so application code never sees a naive value.

    The stored representation stays sortable, so ``WHERE expires_at <= :now``
    behaves correctly on SQLite and on PostgreSQL (where it maps to TIMESTAMPTZ).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):  # pragma: no cover - driver safety net
            raise TypeError(f"Expected a datetime from the database, received {type(value)!r}.")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
