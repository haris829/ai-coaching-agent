"""Fixtures for the tests that need the real database.

Every one of them runs inside a transaction that is rolled back, DDL included - PostgreSQL is
transactional over ``CREATE TABLE``, so a test run leaves the database exactly as it found it
even the first time, when it has to create the tables to have somewhere to write.

The suite is skipped, not failed, when no database is reachable. The parser, the prompt, the
lookup rules and the model client all run with no database at all, and those are the tests worth
having on a laptop with nothing installed.
"""

from __future__ import annotations

import psycopg
import pytest

from qgen.config import load_settings
from qgen.db import normalise_url
from qgen.errors import ConfigurationError


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        return load_settings().database_url
    except ConfigurationError:
        pytest.skip("QGEN_DATABASE_URL is not set")


@pytest.fixture
def conn(database_url: str):
    """A connection that is always rolled back."""
    try:
        connection = psycopg.connect(
            normalise_url(database_url), row_factory=psycopg.rows.dict_row, connect_timeout=5
        )
    except psycopg.OperationalError as exc:
        pytest.skip(f"no database reachable: {exc}")
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
