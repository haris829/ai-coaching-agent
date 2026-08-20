"""Migration / model drift guard, for the whole schema.

Tests build their schema from the models for speed, while deployments run the Alembic migration.
This module is what stops the two from diverging: it applies the migration to a real temporary
database and compares the resulting schema, table by table and constraint by constraint, against
what the models declare.

Without this, a model change that nobody migrated would pass the whole suite and fail in production.

It covers **every** capability, which is why it sits at the top level rather than under one of them.
It arrived with UC-03, whose comparison is far more precise than a bare
``alembic.autogenerate.compare_metadata`` diff, so on merging it replaced the weaker check that had
been living in UC-02's test file — one guard for one schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.metadata import target_metadata  # registers EVERY capability's tables

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def migrated_schema(tmp_path: Path) -> Iterator[dict[str, object]]:
    """Apply the Alembic migration to a scratch database and introspect the result."""
    database_path = tmp_path / "migrated.sqlite"
    url = f"sqlite+pysqlite:///{database_path.as_posix()}"

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    # env.py reads the URL from settings; the explicit option above wins for offline
    # config, so the URL is also set in the environment for the online path.
    import os

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            yield _describe(engine)
        finally:
            engine.dispose()
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture
def model_schema(tmp_path: Path) -> Iterator[dict[str, object]]:
    """Create the schema straight from the models and introspect the result."""
    database_path = tmp_path / "models.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        target_metadata.create_all(engine)
        yield _describe(engine)
    finally:
        engine.dispose()


def _triggers_by_table(engine: object) -> dict[str, list[str]]:
    """The triggers each table carries, read from the live database.

    Compared alongside columns and constraints because a trigger *is* a schema guarantee here —
    every immutability rule in this system is one — and because losing one is silent. SQLite's
    batch migrations rebuild a table to add a column and drop its triggers on the way, so a
    migration that adds a field to an append-only table quietly removes the append-only part.

    That is not hypothetical: UC-09 added three columns to ``qc_configuration_versions`` and
    dropped ``trg_qc_config_version_no_update``, leaving every migrated database willing to edit a
    published configuration version. The suites built their schema from the models and never saw
    it. This comparison is what makes them see it.
    """
    grouped: dict[str, list[str]] = {}
    if engine.dialect.name != "sqlite":  # type: ignore[attr-defined]
        return grouped
    with engine.connect() as connection:  # type: ignore[attr-defined]
        rows = connection.execute(
            text("SELECT name, tbl_name FROM sqlite_master WHERE type = 'trigger'")
        ).all()
    for name, table in rows:
        grouped.setdefault(table, []).append(name)
    return {table: sorted(names) for table, names in grouped.items()}


def _describe(engine: object) -> dict[str, object]:
    """Summarise a live schema in a form that is comparable across databases."""
    inspector = inspect(engine)  # type: ignore[arg-type]
    triggers = _triggers_by_table(engine)
    tables: dict[str, object] = {}

    for table in sorted(inspector.get_table_names()):
        if table == "alembic_version":
            continue
        columns = {
            column["name"]: {
                "type": str(column["type"]),
                "nullable": bool(column["nullable"]),
            }
            for column in inspector.get_columns(table)
        }
        indexes = {
            index["name"]: {
                "columns": list(index["column_names"]),
                "unique": bool(index["unique"]),
            }
            for index in inspector.get_indexes(table)
            if index["name"]
        }
        unique_constraints = sorted(
            (constraint["name"] or "", tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints(table)
        )
        foreign_keys = sorted(
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
            )
            for fk in inspector.get_foreign_keys(table)
        )
        check_constraints = sorted(
            constraint["name"] or "" for constraint in inspector.get_check_constraints(table)
        )
        primary_key = list(inspector.get_pk_constraint(table)["constrained_columns"])

        tables[table] = {
            "columns": columns,
            "indexes": indexes,
            "unique_constraints": unique_constraints,
            "foreign_keys": foreign_keys,
            "check_constraints": check_constraints,
            "primary_key": primary_key,
            "triggers": triggers.get(table, []),
        }

    return tables


def test_migration_creates_the_same_tables_as_the_models(
    migrated_schema: dict[str, object], model_schema: dict[str, object]
) -> None:
    assert sorted(migrated_schema) == sorted(model_schema)


def test_migration_matches_the_models_exactly(
    migrated_schema: dict[str, object], model_schema: dict[str, object]
) -> None:
    for table in sorted(model_schema):
        assert migrated_schema[table] == model_schema[table], (
            f"Schema drift in table {table!r}: the Alembic migration and the ORM models "
            "disagree. Regenerate the migration with "
            "`alembic revision --autogenerate`."
        )


def test_the_trigger_comparison_is_actually_comparing_something(
    model_schema: dict[str, object]
) -> None:
    """A comparison of two empty lists passes and means nothing.

    Named separately from the drift test because the failure modes differ: that one catches a
    trigger the migration lost, and this one catches the comparison quietly stopping working — if
    ``_triggers_by_table`` returned nothing, every table would agree and the drift test would go
    green on a database with no immutability guarantees at all.
    """
    carriers = {
        "qc_configuration_versions": "a published configuration version can never be edited",
        "qr_attempt_results": "a scored result is final",
        "qg_attempt_outcomes": "a pass or fail is a derived fact, written once",
        "qf_feedback_reports": "a generated report is frozen",
        "qk_coaching_messages": "a coaching exchange is a record of what was said",
    }
    for table, why in carriers.items():
        described = model_schema[table]
        assert isinstance(described, dict)
        assert described["triggers"], f"{table} declares no trigger, but {why}"


def test_the_critical_uniqueness_guarantees_are_migrated(
    migrated_schema: dict[str, object]
) -> None:
    """The constraints that carry a business rule, asserted by name.

    The table-by-table comparison above would already catch a missing one, but it would report it as
    an anonymous diff. These are named here so that a failure says *which rule* stopped being
    enforced — and so nobody "fixes" a drift failure by deleting one of them.
    """
    def table(name: str) -> dict:
        found = migrated_schema[name]
        assert isinstance(found, dict)
        return found

    # UC-03: one open attempt per learner and quiz; at most one successful submission per attempt.
    assert table("qd_attempts")["indexes"]["ux_attempt_single_open"]["unique"] is True
    assert table("qd_attempt_submissions")["indexes"]["ux_submission_single_success"]["unique"] is True

    # UC-01: version numbers are unique per quiz, so a version can never be silently overwritten.
    assert ("quiz_id_version_number", ("quiz_id", "version_number")) in (
        table("qc_configuration_versions")["unique_constraints"]
    )

    # UC-02: a question's reference and its optional external ref both identify it uniquely.
    question_indexes = table("qb_questions")["indexes"]
    unique_single_columns = {
        tuple(index["columns"]) for index in question_indexes.values() if index["unique"]
    } | {columns for _name, columns in table("qb_questions")["unique_constraints"]}
    assert ("reference",) in unique_single_columns
    assert ("external_ref",) in unique_single_columns


def test_the_migration_is_reversible(tmp_path: Path) -> None:
    import os

    database_path = tmp_path / "reversible.sqlite"
    url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "base")

        engine = create_engine(url)
        try:
            remaining = {
                name for name in inspect(engine).get_table_names() if name != "alembic_version"
            }
        finally:
            engine.dispose()
        assert remaining == set(), f"downgrade left tables behind: {sorted(remaining)}"
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
