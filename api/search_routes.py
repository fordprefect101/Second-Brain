"""Search routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from api.captures import ConnDep
from api.config import config
from api.notes import get_note_service
from api.providers.obsidian import ObsidianVaultProvider
from api.search import index_captures, index_notes, search

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


class IndexStats(CamelModel):
    notes: dict[str, int]
    captures: dict[str, int]


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
            id=h.id, title=h.title, excerpt=h.excerpt, source=h.source, rank=h.rank
        )
        for h in hits
    ]


@router.post("/search/reindex", response_model=IndexStats)
def reindex(conn: ConnDep) -> IndexStats:
    """Rebuild the index from the sources.

    Manual for now. A file watcher would keep it current automatically, but that
    means handling debouncing, partial writes, and editor temp files — worth doing
    once the index is proven, not while it is being written.

    Safe to call repeatedly: unchanged rows are skipped by content hash, and the
    whole index is rebuildable by design (ADR-002).
    """
    note_stats = {"seen": 0, "indexed": 0, "skipped": 0, "removed": 0}

    if config.vault_path is not None and (config.vault_path / ".obsidian").is_dir():
        note_stats = index_notes(conn, ObsidianVaultProvider(config.vault_path))

    return IndexStats(notes=note_stats, captures=index_captures(conn))


# Imported for its side effect of validating configuration early in dev; the
# dependency itself is used by the notes router.
__all__ = ["router", "get_note_service"]
