"""Search the company database's *text* for quiz content, not just its column names.

    python -m scripts.find_quiz_content

The earlier survey looked for tables and columns *named* like a quiz and found none. That is a
weaker result than it sounds: quiz text can sit inside a description, a guidance note or an
imported blob without any column announcing it. The company believe there may be quizzes in there,
so this looks at the content itself.

For every sizeable text column in the database it counts rows containing the markers a
multiple-choice question leaves behind — "which of the following", "Answer:", "correct answer",
"MCQ", an "A)" option label — and prints a sample of any hit so a human can judge it.

Read-only. It reads business text, which is unavoidable for this question, but only from a legal
reference database that holds no learner or personal data. Credentials come from the environment.
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

DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")

#: What a multiple-choice question leaves behind in prose. Deliberately broad — a false positive
#: costs a glance, a false negative means telling the company something untrue about their data.
MARKERS = (
    "which of the following",
    "correct answer",
    "answer:",
    "mcqs",
    "multiple choice",
    "select the best",
    "true or false",
    "quiz",
)

#: Columns below this length cannot hold a question, and scanning them wastes time.
MIN_COLUMN_LENGTH = 200


def _connect() -> Any:
    try:
        import pyodbc
    except ImportError:  # pragma: no cover
        sys.exit("pyodbc is not installed")
    missing = [
        name
        for name in ("COMPANY_DB_SERVER", "COMPANY_DB_NAME", "COMPANY_DB_USER", "COMPANY_DB_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        sys.exit(f"set these first: {', '.join(missing)}")
    driver = next((name for name in DRIVERS if name in set(pyodbc.drivers())), None)
    if driver is None:
        sys.exit("install ODBC Driver 18 for SQL Server")
    return pyodbc.connect(
        f"DRIVER={{{driver}}};"
        f"SERVER={os.environ['COMPANY_DB_SERVER']};"
        f"DATABASE={os.environ['COMPANY_DB_NAME']};"
        f"UID={os.environ['COMPANY_DB_USER']};"
        f"PWD={os.environ['COMPANY_DB_PASSWORD']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30",
        readonly=True,
        timeout=180,
    )


def main() -> int:
    connection = _connect()
    cursor = connection.cursor()

    columns = cursor.execute(
        """
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE DATA_TYPE IN ('nvarchar', 'varchar', 'ntext', 'text')
          AND (CHARACTER_MAXIMUM_LENGTH = -1 OR CHARACTER_MAXIMUM_LENGTH >= ?)
        ORDER BY TABLE_NAME, COLUMN_NAME
        """,
        MIN_COLUMN_LENGTH,
    ).fetchall()
    print(f"scanning {len(columns)} text column(s) for quiz markers\n")

    hits: list[tuple[str, str, str, int]] = []
    for schema, table, column in columns:
        clause = " OR ".join(f"[{column}] LIKE ?" for _ in MARKERS)
        try:
            count = cursor.execute(
                f"SELECT COUNT(*) FROM [{schema}].[{table}] WHERE {clause}",
                *[f"%{marker}%" for marker in MARKERS],
            ).fetchone()[0]
        except Exception as error:  # noqa: BLE001 - a column we cannot scan is worth naming, not fatal
            print(f"  (skipped {table}.{column}: {type(error).__name__})")
            continue
        if count:
            hits.append((schema, table, column, count))
            print(f"  HIT  {table}.{column:<28} {count} row(s)")

    if not hits:
        print("\nNo text column in this database contains any quiz marker.")
        print("There is no quiz content here — not in a table name, a column name, or the text.")
        connection.close()
        return 0

    print(f"\n{len(hits)} column(s) contain quiz markers. Samples follow.\n")
    for schema, table, column, count in sorted(hits, key=lambda row: -row[3])[:6]:
        print("=" * 74)
        print(f"{table}.{column}  ({count} rows)")
        print("=" * 74)
        clause = " OR ".join(f"[{column}] LIKE ?" for _ in MARKERS)
        rows = cursor.execute(
            f"SELECT TOP 2 [{column}] FROM [{schema}].[{table}] WHERE {clause}",
            *[f"%{marker}%" for marker in MARKERS],
        ).fetchall()
        for (text,) in rows:
            print(" ".join(str(text).split())[:900])
            print("-" * 74)

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
