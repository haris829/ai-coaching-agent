"""Migration CLI.

    python -m uc01.persistence.migrate            # apply pending migrations
    python -m uc01.persistence.migrate --status   # show applied migrations
    python -m uc01.persistence.migrate --path ./data/other.sqlite3
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_settings
from .db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UC-01 standalone persistence migrations")
    parser.add_argument("--path", help="SQLite file path (defaults to UC01_DATABASE_PATH)")
    parser.add_argument("--status", action="store_true", help="only report applied migrations")
    args = parser.parse_args(argv)

    settings = load_settings()
    path = args.path or settings.database_path
    database = Database(path)
    try:
        if args.status:
            applied = database.applied_migrations()
            print(f"database: {path}")
            print("applied migrations:" if applied else "applied migrations: none")
            for version in applied:
                print(f"  - {version}")
            return 0

        newly_applied = database.migrate()
        print(f"database: {path}")
        if newly_applied:
            for version in newly_applied:
                print(f"applied {version}")
        else:
            print("already up to date")
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    sys.exit(main())
