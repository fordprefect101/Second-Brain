"""Search routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from api.captures import ConnDep
from api.config import config
from api.config import REPO_ROOT
from api.notes import get_note_service
from api.providers.github import GitHubProvider
from api.providers.google_calendar import GoogleCalendarProvider
from api.providers.google_tasks import GoogleTasksProvider
from api.providers.obsidian import ObsidianVaultProvider
from api.search import (
    index_captures,
    index_events,
    index_notes,
    index_repositories,
    index_tasks,
    search,
)

router = APIRouter(tags=["search"])


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SearchResult(CamelModel):
    id: UUID
    title: str
    excerpt: str
    # Required, not optional. A result that cannot name its source is a bug
    # (Plan.md §12).
    source: str
    rank: float
    # Source-dependent meaning — event start, task due date, note edit, repo push.
    # Nullable because nothing guarantees a row has one.
    modified_at: datetime | None = None


class IndexStats(CamelModel):
    """Per-source counts. A source that is not connected reports zeros rather than
    failing the whole reindex — partial coverage beats no index at all."""

    notes: dict[str, int]
    captures: dict[str, int]
    events: dict[str, int]
    tasks: dict[str, int]
    repositories: dict[str, int]
    errors: dict[str, str] = {}


@router.get("/search", response_model=list[SearchResult])
def search_endpoint(
    conn: ConnDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    source: str | None = None,
) -> list[SearchResult]:
    """Search everything indexed. Empty list rather than 404 when nothing matches."""
    hits = search(conn, q, limit=limit, source=source)
    return [
        SearchResult(
            id=h.id,
            title=h.title,
            excerpt=h.excerpt,
            source=h.source,
            rank=h.rank,
            modified_at=h.modified_at,
        )
        for h in hits
    ]


@router.post("/search/reindex", response_model=IndexStats)
def reindex(conn: ConnDep) -> IndexStats:
    """Rebuild the index on demand, from the UI's 'rebuild index' button."""
    return run_reindex(conn)


def run_reindex(conn: psycopg.Connection) -> IndexStats:
    """Rebuild the index from every connected source.

    A plain function rather than only a route, because startup runs this too when
    the index is stale (api/main.py). One implementation, two callers — a second
    copy that drifts from this one is the failure ADR-006 names.

    NOTE: `conn` must have a dict_row factory. index_source reads rows by name.

    Every source is attempted independently. Google being disconnected must not
    stop Obsidian being indexed: one broken integration should degrade the index,
    not empty it.
    """
    empty = {"seen": 0, "indexed": 0, "skipped": 0, "removed": 0}
    stats: dict[str, dict[str, int]] = {}
    errors: dict[str, str] = {}

    def attempt(name: str, fn) -> None:
        try:
            stats[name] = fn()
        except Exception as exc:
            # Deliberately broad: any provider failure — auth expired, rate
            # limited, network down — should cost that one source, not the run.
            stats[name] = dict(empty)
            errors[name] = f"{type(exc).__name__}: {exc}"[:200]

    if config.vault_path is not None and (config.vault_path / ".obsidian").is_dir():
        attempt("notes", lambda: index_notes(conn, ObsidianVaultProvider(config.vault_path)))
    else:
        stats["notes"] = dict(empty)

    attempt("captures", lambda: index_captures(conn))
    attempt("events", lambda: index_events(conn, GoogleCalendarProvider(REPO_ROOT)))
    attempt("tasks", lambda: index_tasks(conn, GoogleTasksProvider(REPO_ROOT)))
    attempt("repositories", lambda: index_repositories(conn, GitHubProvider()))

    return IndexStats(
        notes=stats["notes"],
        captures=stats["captures"],
        events=stats["events"],
        tasks=stats["tasks"],
        repositories=stats["repositories"],
        errors=errors,
    )
