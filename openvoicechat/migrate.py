"""Apply versioned SQLite migrations for local conversation history."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from .history import DATA_DIR, DATABASE

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class DatabaseMigrationError(RuntimeError):
    """Raised when SQLite migrations cannot be applied."""


def migrate() -> list[int]:
    try:
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
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        raise DatabaseMigrationError(
            f"Failed to apply database migrations to {DATABASE}: {exc}"
        ) from exc


def main() -> None:
    try:
        applied = migrate()
        if applied:
            print("Applied conversation database migrations: " + ", ".join(map(str, applied)))
        else:
            print("Conversation database is up to date.")
    except (DatabaseMigrationError, sqlite3.Error, OSError, RuntimeError) as exc:
        print(f"Error applying conversation database migrations: {exc}", file=sys.stderr)
        print(
            "Recovery guidance: Check directory permissions or database integrity, then run `python -m openvoicechat.migrate` to retry.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
