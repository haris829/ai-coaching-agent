"""Read-only inventory of the company's PostgreSQL database.

    python -m scripts.inspect_company_postgres

The SQL Server database (``larry-legal``) turned out to hold scraped legal content and no learning
platform: no users, no enrolments, no assessments. Their Postgres is a different story — it backs
the live product, and its ``public`` schema belongs to a Prisma service. This looks for what SQL
Server was missing: the real users, courses and anything assessment-shaped.

It also answers a question we have to get right before writing a single table: **which schemas are
already taken.** Their configuration shows ``public`` owned by the Node/Prisma service and ``larry``
owned by LarryAI, each keeping out of the other's way. The quiz agent must do the same rather than
scattering ``qc_``/``qd_``/``qk_`` tables into somebody else's namespace.

READ-ONLY, AND STRUCTURALLY SO
------------------------------
Every statement is a ``SELECT`` against a system catalogue — ``information_schema``, ``pg_*``. No
business row is read, so no personal data is touched, which matters far more here than it did on the
content database: this one has real users in it. :func:`_rows` refuses anything that is not a
``SELECT``, and the session is opened read-only.

Credentials come from ``COMPANY_PG_DSN`` in the environment. Nothing is hard-coded and nothing is
written to disk.
"""

from __future__ import annotations

import os
import sys
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

#: Table names worth a closer look — what the SQL Server database did not have.
INTERESTING = (
    "user",
    "learner",
    "student",
    "account",
    "profile",
    "course",
    "lesson",
    "module",
    "enrol",
    "enroll",
    "quiz",
    "question",
    "assess",
    "exam",
    "attempt",
    "result",
    "score",
    "progress",
    "certif",
    "cpd",
)


def _rows(cursor: Any, statement: str, *params: Any) -> list[Any]:
    if not statement.lstrip().upper().startswith("SELECT"):
        raise ValueError("this script issues SELECT statements only")
    cursor.execute(statement, params or None)
    return cursor.fetchall()


def _heading(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> int:
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        sys.exit("psycopg is not installed")

    dsn = os.environ.get("COMPANY_PG_DSN")
    if not dsn:
        sys.exit("set COMPANY_PG_DSN first")

    # Prisma's `?schema=` is not a libpq parameter; psycopg would reject it.
    dsn = dsn.replace("?schema=public&", "?").replace("&schema=public", "")

    with psycopg.connect(dsn, connect_timeout=30, autocommit=True) as connection:
        connection.read_only = True
        cursor = connection.cursor()

        print(f"connected read-only to {connection.info.dbname} on {connection.info.host}")

        _heading("1. Schemas, and how many tables each holds")
        for schema, count in _rows(
            cursor,
            """
            SELECT table_schema, COUNT(*)
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            GROUP BY table_schema ORDER BY table_schema
            """,
        ):
            print(f"  {schema:<24} {count:>4} tables")

        _heading("2. Every table, with its live row estimate")
        tables = _rows(
            cursor,
            """
            SELECT schemaname, relname, n_live_tup
            FROM pg_stat_user_tables
            ORDER BY schemaname, relname
            """,
        )
        print(f"{len(tables)} tables")
        for schema, table, live in tables:
            print(f"  {schema}.{table:<46} {live:>10} rows")

        _heading("3. The tables that matter to us")
        relevant = [
            (schema, table)
            for schema, table, _live in tables
            if any(fragment in str(table).lower() for fragment in INTERESTING)
        ]
        if not relevant:
            print("  (nothing matched by name)")
        for schema, table in relevant:
            columns = _rows(
                cursor,
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                schema,
                table,
            )
            print(f"\n  {schema}.{table}")
            for name, data_type, nullable in columns:
                null = "NULL" if nullable == "YES" else "NOT NULL"
                print(f"    {name:<34} {data_type:<26} {null}")

        _heading("4. Can this account create a schema?")
        # The one operational fact that decides whether we can deploy at all.
        for name, can_create, is_super in _rows(
            cursor,
            "SELECT current_user, has_database_privilege(current_user, current_database(), "
            "'CREATE'), usesuper FROM pg_user WHERE usename = current_user",
        ):
            print(f"  user           : {name}")
            print(f"  CREATE on db   : {can_create}")
            print(f"  superuser      : {is_super}")

        _heading("5. Extensions installed")
        for (name,) in _rows(
            cursor, "SELECT extname FROM pg_extension ORDER BY extname"
        ):
            print(f"  {name}")

    print("\ndone — nothing was modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
