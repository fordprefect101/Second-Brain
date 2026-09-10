"""Test fixtures.

Two rules this file exists to enforce:

  1. Tests never touch your real database. A separate `personalos_test` database is
     created, used, and torn down. A test suite that can destroy real data is one
     you stop running.
  2. Tests never touch your real vault. Every filesystem test gets a throwaway
     directory — which matters more than usual here, because the code under test
     writes and deletes files.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DB_NAME = "personalos_test"


def _swap_database(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


def _resolve_urls() -> tuple[str, str]:
    """(url for the test database, url for the maintenance database)."""
    env = dotenv_values(REPO_ROOT / ".env")
    url = os.environ.get("DATABASE_URL") or env.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; cannot run database tests.")
    return _swap_database(url, TEST_DB_NAME), _swap_database(url, "postgres")


TEST_URL, ADMIN_URL = _resolve_urls()

# Set BEFORE importing anything from api. api.config reads DATABASE_URL at import
# time, and python-dotenv does not override variables already in the environment —
# so this wins over .env and the app connects to the test database.
os.environ["DATABASE_URL"] = TEST_URL


def _create_test_database() -> None:
    # CREATE DATABASE cannot run inside a transaction, hence autocommit.
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select 1 from pg_database where datname = %s", (TEST_DB_NAME,))
        if cur.fetchone() is None:
            cur.execute(f'create database "{TEST_DB_NAME}"')


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    """Create the test database and apply the schema once per run."""
    _create_test_database()

    from api.database import ensure_schema

    ensure_schema()


@pytest.fixture
def db(database):
    """A connection, with every table emptied afterwards.

    Truncate rather than rollback: the code under test calls conn.commit() itself,
    so wrapping tests in a transaction would not isolate them.
    """
    from psycopg.rows import dict_row

    from api.database import connect

    conn = connect()
    conn.row_factory = dict_row
    try:
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "truncate capture_items, entity_map, search_index, "
                "note_snapshots, connections, sync_state cascade"
            )
        conn.commit()
        conn.close()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """An empty throwaway vault. `.obsidian/` makes it a valid vault root."""
    root = tmp_path / "vault"
    (root / ".obsidian").mkdir(parents=True)
    return root


@pytest.fixture
def provider(vault: Path):
    from api.providers.obsidian import ObsidianVaultProvider

    return ObsidianVaultProvider(vault)


def write_note(vault: Path, relative: str, content: str) -> Path:
    """Helper: create a note at a vault-relative path."""
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
