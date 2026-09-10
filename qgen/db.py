"""The PostgreSQL connection, and nothing else.

One small module so every other module takes a live connection rather than knowing how to make
one - which is what lets the storage tests run against a real database and the domain tests run
against no database at all.

THIS PACKAGE TARGETS POSTGRESQL, NOT "A DATABASE"
--------------------------------------------------
Developing against SQLite and deploying to PostgreSQL breaks in two places that both pass
locally: ``AND is_correct = 1`` (SQLite stores booleans as integers and accepts the comparison;
PostgreSQL fails with ``operator does not exist: boolean = integer``) and JSON assembly functions
that do not exist on both. So there is no SQLite path here at all, the boolean form is checked by
``tests/test_portability.py``, and JSON is assembled in Python.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def normalise_url(url: str) -> str:
    """A SQLAlchemy-style URL as psycopg wants it.

    The brief hands out ``postgresql+psycopg://...``, which is SQLAlchemy's dialect notation and
    not a scheme libpq understands. Accepting both spellings is one line here and saves a
    confusing connection error for whoever pastes the URL from the brief.
    """
    for dialect in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(dialect):
            return "postgresql://" + url[len(dialect) :]
    return url


@contextmanager
def connect(url: str) -> Iterator[psycopg.Connection]:
    """A connection with dict rows, committed on success and rolled back on an exception.

    Rolled back rather than left open: a generation run that fails part way through must not
    leave half a paper in the database, because a half-written run looks exactly like a complete
    short one to anybody reading the table afterwards.
    """
    conn = psycopg.connect(normalise_url(url), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
