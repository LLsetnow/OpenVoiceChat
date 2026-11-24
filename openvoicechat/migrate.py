"""Apply versioned SQLite migrations for local conversation history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .history import DATA_DIR, DATABASE

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def migrate() -> list[int]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    applied: list[int] = []
    with sqlite3.connect(DATABASE, timeout=10) as connection:
        try:
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            current = 0

        for migration in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version = int(migration.name.split("_", 1)[0])
            if version <= current:
                continue
            connection.executescript(migration.read_text(encoding="utf-8"))
            applied.append(version)

    return applied


def main() -> None:
    applied = migrate()
    if applied:
        print("Applied conversation database migrations: " + ", ".join(map(str, applied)))
    else:
        print("Conversation database is up to date.")


if __name__ == "__main__":
    main()
