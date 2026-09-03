"""Read-only inventory of the company's SQL Server database.

    python -m scripts.inspect_company_db

Written to answer one question: which of the tables in ``quiz_configuration`` duplicate something
the company already owns? The company has told us their courses and quizzes already exist, so
``qc_courses`` and ``qc_quizzes`` come out and the module reads theirs instead. This script is how we
find the real column names to read.

READ-ONLY, AND STRUCTURALLY SO
------------------------------
* the connection is opened with ``readonly=True``;
* every statement is a ``SELECT`` against a **system catalogue** — ``sys.tables``,
  ``INFORMATION_SCHEMA.COLUMNS``, ``sys.foreign_keys``. None of them reads a row of business data,
  so nothing here can expose learner information;
* there is no ``INSERT``, ``UPDATE``, ``DELETE``, ``CREATE`` or ``DROP`` anywhere in this file, and
  :func:`_rows` refuses any statement that does not begin with ``SELECT``.

The account we were given (``larry_readonly``) cannot write anyway. The guards above mean that is
belt-and-braces rather than the only thing standing between a survey and an accident.

CREDENTIALS COME FROM THE ENVIRONMENT
-------------------------------------
Nothing is hard-coded and nothing is written to disk. Set these before running::

    COMPANY_DB_SERVER=loophole-larry-db.database.windows.net,1433
    COMPANY_DB_NAME=larry-legal
    COMPANY_DB_USER=larry_readonly
    COMPANY_DB_PASSWORD=...

The password never reaches the command line (where it would land in shell history) and never reaches
a file in this repository. Rotate it once the integration work is done: a shared read-only password
that has travelled through a chat log should not stay live indefinitely.
"""

from __future__ import annotations

import os
import sys
from typing import Any

#: Tables whose names suggest they hold something quiz configuration currently duplicates.
INTERESTING = (
    "cours",
    "quiz",
    "question",
    "user",
    "learner",
    "enrol",
    "enroll",
    "attempt",
    "assess",
    "module",
    "exam",
    "test",
)

DRIVERS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
)


def _connect() -> Any:
    try:
        import pyodbc
    except ImportError:  # pragma: no cover - environment problem, not a code path
        sys.exit("pyodbc is not installed: python -m pip install pyodbc")

    missing = [
        name
        for name in ("COMPANY_DB_SERVER", "COMPANY_DB_NAME", "COMPANY_DB_USER", "COMPANY_DB_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        sys.exit(f"set these environment variables first: {', '.join(missing)}")

    available = set(pyodbc.drivers())
    driver = next((name for name in DRIVERS if name in available), None)
    if driver is None:
        sys.exit(
            "no modern SQL Server ODBC driver found. Install one with:\n"
            "  winget install --id Microsoft.msodbcsql.18 --exact\n"
            f"drivers present: {sorted(available)}"
        )

    return pyodbc.connect(
        f"DRIVER={{{driver}}};"
        f"SERVER={os.environ['COMPANY_DB_SERVER']};"
        f"DATABASE={os.environ['COMPANY_DB_NAME']};"
        f"UID={os.environ['COMPANY_DB_USER']};"
        f"PWD={os.environ['COMPANY_DB_PASSWORD']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30",
        readonly=True,
        timeout=60,
    )


def _rows(cursor: Any, statement: str) -> list[Any]:
    """Run one read-only statement.

    The guard is not decoration: a survey script is exactly the kind of thing that acquires a
    "just fix this one row" statement six months later, and this makes that a failure rather than a
    quiet edit to somebody's production database.
    """
    if not statement.lstrip().upper().startswith("SELECT"):
        raise ValueError("this script issues SELECT statements only")
    cursor.execute(statement)
    return cursor.fetchall()


def _heading(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> int:
    connection = _connect()
    cursor = connection.cursor()

    print(f"connected read-only to {os.environ['COMPANY_DB_NAME']}")

    _heading("1. Every table, with row counts")
    tables = _rows(
        cursor,
        """
        SELECT s.name, t.name, SUM(ISNULL(p.rows, 0))
        FROM sys.tables t
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        LEFT JOIN sys.partitions p
               ON p.object_id = t.object_id AND p.index_id IN (0, 1)
        GROUP BY s.name, t.name
        ORDER BY s.name, t.name
        """,
    )
    print(f"{len(tables)} tables")
    for schema, table, count in tables:
        print(f"  {schema}.{table:<45} {count:>10} rows")

    names = {str(table).lower() for _schema, table, _count in tables}
    matched = sorted(
        name for name in names if any(fragment in name for fragment in INTERESTING)
    )
    _heading("2. Tables that look relevant to quiz configuration")
    print("  " + ("\n  ".join(matched) if matched else "(none matched by name)"))

    _heading("3. Columns of those tables")
    for schema, table, _count in tables:
        if not any(fragment in str(table).lower() for fragment in INTERESTING):
            continue
        columns = _rows(
            cursor,
            f"""
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}'
            ORDER BY ORDINAL_POSITION
            """,
        )
        print(f"\n  {schema}.{table}")
        for name, data_type, length, nullable in columns:
            size = f"({length})" if length not in (None, -1) else ""
            null = "NULL" if nullable == "YES" else "NOT NULL"
            print(f"    {name:<38} {data_type}{size:<10} {null}")

    _heading("4. Primary keys")
    for schema, table, pk_column in _rows(
        cursor,
        """
        SELECT s.name, t.name, c.name
        FROM sys.key_constraints kc
        JOIN sys.tables t   ON t.object_id = kc.parent_object_id
        JOIN sys.schemas s  ON s.schema_id = t.schema_id
        JOIN sys.index_columns ic
             ON ic.object_id = t.object_id AND ic.index_id = kc.unique_index_id
        JOIN sys.columns c
             ON c.object_id = t.object_id AND c.column_id = ic.column_id
        WHERE kc.type = 'PK'
        ORDER BY s.name, t.name, ic.key_ordinal
        """,
    ):
        print(f"  {schema}.{table:<45} {pk_column}")

    _heading("5. Foreign keys")
    for from_table, from_column, to_table, to_column in _rows(
        cursor,
        """
        SELECT OBJECT_NAME(fk.parent_object_id),
               COL_NAME(fkc.parent_object_id, fkc.parent_column_id),
               OBJECT_NAME(fk.referenced_object_id),
               COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id)
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc
             ON fkc.constraint_object_id = fk.object_id
        ORDER BY 1, 2
        """,
    ):
        print(f"  {from_table}.{from_column}  ->  {to_table}.{to_column}")

    _heading("6. Other databases visible on this server")
    try:
        for (name,) in _rows(cursor, "SELECT name FROM sys.databases ORDER BY name"):
            print(f"  {name}")
    except Exception as exc:  # noqa: BLE001 - a scoped login cannot list databases; that is an answer
        print(f"  (cannot list: {type(exc).__name__})")

    _heading("7. Any column anywhere named like a learning platform")
    found = _rows(
        cursor,
        """
        SELECT TABLE_NAME, COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE COLUMN_NAME LIKE '%quiz%'    OR COLUMN_NAME LIKE '%learner%'
           OR COLUMN_NAME LIKE '%enrol%'   OR COLUMN_NAME LIKE '%attempt%'
           OR COLUMN_NAME LIKE '%score%'   OR COLUMN_NAME LIKE '%question%'
           OR COLUMN_NAME LIKE '%student%' OR COLUMN_NAME LIKE '%user%'
           OR COLUMN_NAME LIKE '%assess%' OR COLUMN_NAME LIKE '%exam%'
           OR COLUMN_NAME LIKE '%module%' OR COLUMN_NAME LIKE '%lesson%'
           OR COLUMN_NAME LIKE '%grade%'  OR COLUMN_NAME LIKE '%mark%'
           OR COLUMN_NAME LIKE '%result%' OR COLUMN_NAME LIKE '%progress%'
           OR COLUMN_NAME LIKE '%cpd%'    OR COLUMN_NAME LIKE '%certif%'
           OR COLUMN_NAME LIKE '%train%'  OR COLUMN_NAME LIKE '%syllab%'
        ORDER BY TABLE_NAME, COLUMN_NAME
        """,
    )
    print(f"  {len(found)} matching columns")
    for table, column in found:
        print(f"  {table}.{column}")

    connection.close()
    print("\ndone — nothing was modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
