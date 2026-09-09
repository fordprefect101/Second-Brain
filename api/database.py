"""Database connection and idempotent schema application.

Deliberately small. Per ADR-003 there is no ORM: callers write SQL, and every query
is parameterised — never f-strings or % formatting, which is the one way hand-written
SQL goes badly wrong.

Runnable on its own, to apply the schema without starting the API:

    .venv/bin/python -m api.database
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from api.config import config

SCHEMA_PATH = Path(__file__).parent / "db" / "schema.sql"

EXPECTED_TABLES = {
    "capture_items",
    "connections",
    "entity_map",
    "note_snapshots",
    "search_index",
    "sync_state",
}


class DatabaseUnavailable(RuntimeError):
    """Raised when Postgres cannot be reached.

    Distinct from a query error on purpose. For a local daily-use tool the most
    common failure by far is "the container isn't running", and that deserves an
    actionable message rather than a connection stack trace.
    """


def connect() -> psycopg.Connection:
    """Open a connection. Caller is responsible for closing (use as a context manager).

    DATABASE_URL is validated at import time by api.config, so by the time this runs
    the only remaining failure is the database being unreachable.
    """
    try:
        return psycopg.connect(config.database_url)
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable(
            f"Cannot reach Postgres at {config.safe_database_url}.\n"
            f"Is the container running?  docker compose up -d\n"
            f"Underlying error: {exc}"
        ) from exc


def ensure_schema(conn: psycopg.Connection | None = None) -> None:
    """Apply db/schema.sql.

    Safe to run repeatedly — every statement in the schema is IF NOT EXISTS. This is
    the whole migration story for now (ADR-003): the schema is small and mostly
    append-only, and the database is rebuildable (ADR-002), so a botched change is
    recovered by dropping and reindexing rather than by a down-migration.
    """
    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    owns_connection = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        if owns_connection:
            conn.close()


def list_tables(conn: psycopg.Connection) -> set[str]:
    """Table names in the public schema."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select table_name
              from information_schema.tables
             where table_schema = 'public'
               and table_type = 'BASE TABLE'
            """
        )
        return {row[0] for row in cur.fetchall()}


def main() -> int:
    print(f"Connecting to {config.safe_database_url}")
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("select version()")
                version = cur.fetchone()[0]
            print(f"  {version.split(',')[0]}")

            ensure_schema(conn)
            print("Schema applied.")

            found = list_tables(conn)
            missing = EXPECTED_TABLES - found
            extra = found - EXPECTED_TABLES

            for table in sorted(found):
                marker = "+" if table in EXPECTED_TABLES else "?"
                print(f"  {marker} {table}")

            if missing:
                print(f"\nMISSING: {', '.join(sorted(missing))}", file=sys.stderr)
                return 1
            if extra:
                print(f"\nUnexpected tables: {', '.join(sorted(extra))}", file=sys.stderr)

            # Idempotency is a claim worth testing, not assuming.
            ensure_schema(conn)
            print("\nRe-applied cleanly — schema is idempotent.")
            return 0

    except DatabaseUnavailable as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
