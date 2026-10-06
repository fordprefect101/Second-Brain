"""Note routes.

The layering from docs/architecture/phase-0.md, made concrete:

    route  ->  NoteService (Protocol)  ->  ObsidianVaultProvider  ->  filesystem

This module imports the Protocol, not the provider. The single line that picks
which provider to use lives in get_note_service() — that is the whole seam. Adding
Notion in Phase 4 means writing a provider and changing that function.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from pydantic import BaseModel, Field

from api import link_rules, note_writes, tags as tag_rules
from api.captures import ConnDep
from api.config import config
from api.entities import lookup_provider_id, resolve_ids
from api.graph import build_graph
from api.providers.obsidian import ObsidianVaultProvider
from api.services import NoteService

router = APIRouter(prefix="/notes", tags=["notes"])


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Note(CamelModel):
    """The domain shape, matching web/src/types.ts.

    No `path` field. The UI must not be able to tell that an Obsidian note is a
    file on disk — otherwise Notion pages, which have no path, break every
    component in Phase 4.
    """

    id: UUID
    title: str
    excerpt: str
    tags: list[str]
    modified_at: datetime
    source: str


class NoteDetail(Note):
    body: str


def get_note_service() -> NoteService:
    """Pick the provider. The only place a concrete provider is named.

    Returns 503 rather than crashing when no vault is configured — an unconfigured
    integration is a normal state for this app, not an error.
    """
    if config.vault_path is None:
        raise HTTPException(
            status_code=503,
            detail="No Obsidian vault configured. Set OBSIDIAN_VAULT_PATH in .env",
        )
    if not (config.vault_path / ".obsidian").is_dir():
        raise HTTPException(
            status_code=503,
            detail=(
                f"{config.vault_path} is not a vault root — no .obsidian/ directory. "
                "Point OBSIDIAN_VAULT_PATH at the folder containing .obsidian/"
            ),
        )
    return ObsidianVaultProvider(config.vault_path)


ServiceDep = Annotated[NoteService, Depends(get_note_service)]


@router.get("", response_model=list[Note])
def list_notes(
    service: ServiceDep,
    conn: ConnDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[Note]:
    """List notes, newest first.

    Two steps, and the second is the interesting one: the provider returns its own
    identifiers, then entity_map converts them into stable internal ids before
    anything leaves this layer.
    """
    provider_notes = service.list_notes(limit=limit)

    ids = resolve_ids(
        conn,
        provider=service.source_id,
        entity_type="note",
        provider_ids=[n.provider_id for n in provider_notes],
    )

    return [
        Note(
            id=ids[n.provider_id],
            title=n.title,
            excerpt=n.excerpt,
            tags=n.tags,
            modified_at=n.modified_at,
            source=service.source_id,
        )
        for n in provider_notes
    ]


class GraphNode(CamelModel):
    id: UUID
    title: str
    links: int  # lines touching this note, either direction: sets its dot size


class GraphEdge(CamelModel):
    source: UUID
    target: UUID


class NoteGraph(CamelModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    unresolved: int


# Declared before /{note_id}: FastAPI tries routes in order.
@router.get("/graph", response_model=NoteGraph)
def note_graph(service: ServiceDep, conn: ConnDep) -> NoteGraph:
    """Every note, and every [[link]] between two of them (api/graph.py)."""
    graph = build_graph(service.list_notes(limit=10_000, with_body=True))
    ids = resolve_ids(
        conn,
        provider=service.source_id,
        entity_type="note",
        provider_ids=[n.provider_id for n in graph.nodes],
    )
    degree = Counter(end for edge in graph.edges for end in edge)
    return NoteGraph(
        nodes=[
            GraphNode(id=ids[n.provider_id], title=n.title, links=degree[n.provider_id])
            for n in graph.nodes
        ],
        edges=[GraphEdge(source=ids[a], target=ids[b]) for a, b in graph.edges],
        unresolved=graph.unresolved,
    )


@router.get("/graph/problems", response_model=list[str])
def note_graph_problems(service: ServiceDep) -> list[str]:
    """Every link in the vault that breaks the graph's rules (api/link_rules.py).

    Writes through this API are refused before they can break one; this is for
    notes edited by hand in Obsidian, which nothing here can stop.
    """
    return link_rules.problems(service.list_notes(limit=10_000, with_body=True))


@router.get("/{note_id}", response_model=NoteDetail)
def get_note(note_id: UUID, service: ServiceDep, conn: ConnDep) -> NoteDetail:
    """Full note body, fetched on demand.

    Bodies are never cached (Plan.md §2) — a cached copy goes stale the moment the
    file is edited, and a second copy that can diverge makes this a second source of
    truth. Reading from disk each time is the cost of staying a control layer.
    """
    mapping = lookup_provider_id(conn, note_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Unknown note id")

    provider, provider_id = mapping
    if provider != service.source_id:
        raise HTTPException(status_code=404, detail=f"No provider for '{provider}'")

    note = service.get_note(provider_id)
    if note is None:
        # The entity_map row outlived the file — deleted or renamed in the vault.
        raise HTTPException(status_code=404, detail="Note no longer exists in the vault")

    return NoteDetail(
        id=note_id,
        title=note.title,
        excerpt=note.excerpt,
        tags=note.tags,
        modified_at=note.modified_at,
        source=service.source_id,
        body=note.body or "",
    )


# -- writes ----------------------------------------------------------------
#
# For notes filed from outside this app (docs/m1-setup.md). The logic, and why it
# is ordered the way it is, lives in api/note_writes.py; these only translate.


class NoteCreate(CamelModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1)
    type: str
    project: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list)
    folder: str = ""
    allow_new_tags: bool = False
    # Project pages only (type "project").
    status: str | None = Field(default=None, max_length=40)
    repos: list[str] = Field(default_factory=list)


class NoteUpdate(CamelModel):
    body: str = Field(min_length=1)
    # The modifiedAt the caller read; the write is refused if the file moved on.
    base_modified_at: datetime


class NoteWritten(CamelModel):
    id: UUID
    path: str
    tags: list[str]
    new_tags: list[str]


class UndoResult(CamelModel):
    id: UUID
    result: str


def _refuse(exc: Exception) -> HTTPException:
    if isinstance(exc, note_writes.TagNeedsConfirmation):
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "tag": exc.tag,
                "similarTo": exc.similar.tag if exc.similar else None,
                "checkedMeaning": exc.checked_meaning,
            },
        )
    if isinstance(exc, OSError):
        return HTTPException(status_code=500, detail=f"Could not write to the vault: {exc}")
    return HTTPException(status_code=409, detail=str(exc))


@router.post("", response_model=NoteWritten, status_code=201)
def create_note(payload: NoteCreate, service: ServiceDep, conn: ConnDep) -> NoteWritten:
    """File a new note in the vault. Reversible via /notes/{id}/undo."""
    try:
        written = note_writes.create_note(
            conn,
            service,
            title=payload.title,
            body=payload.body,
            note_type=payload.type,
            project=payload.project,
            topics=payload.tags,
            folder=payload.folder,
            allow_new_tags=payload.allow_new_tags,
            status=payload.status,
            repos=payload.repos,
        )
    except (note_writes.NoteWriteError, ValueError, OSError) as exc:
        raise _refuse(exc) from exc
    return NoteWritten(
        id=written.entity_id,
        path=written.provider_id,
        tags=written.tags,
        new_tags=written.new_tags,
    )


@router.put("/{note_id}", response_model=NoteWritten)
def update_note(
    note_id: UUID, payload: NoteUpdate, service: ServiceDep, conn: ConnDep
) -> NoteWritten:
    """Replace a note's text, keeping its frontmatter. Reversible via undo."""
    try:
        written = note_writes.update_note(
            conn,
            service,
            note_id,
            body=payload.body,
            base_modified_at=payload.base_modified_at,
        )
    except (note_writes.NoteWriteError, OSError) as exc:
        raise _refuse(exc) from exc
    return NoteWritten(
        id=written.entity_id, path=written.provider_id, tags=written.tags, new_tags=[]
    )


@router.post("/{note_id}/undo", response_model=UndoResult)
def undo_note_write(note_id: UUID, service: ServiceDep, conn: ConnDep) -> UndoResult:
    """Reverse the latest API write to this note."""
    try:
        result = note_writes.undo_last_write(conn, service, note_id)
    except (note_writes.NoteWriteError, OSError) as exc:
        raise _refuse(exc) from exc
    return UndoResult(id=note_id, result=result)


# -- tags ------------------------------------------------------------------

tags_router = APIRouter(prefix="/tags", tags=["tags"])


class TagCount(CamelModel):
    tag: str
    notes: int


class TagList(CamelModel):
    types: list[str]
    tags: list[TagCount]


class TagSuggestRequest(CamelModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = ""


class TagSuggestion(CamelModel):
    tag: str
    score: float
    by: str


class TagSuggestions(CamelModel):
    suggestions: list[TagSuggestion]
    # False when the embedding model was unreachable and only words were matched.
    checked_meaning: bool


@tags_router.get("", response_model=TagList)
def list_tags(service: ServiceDep) -> TagList:
    """Every tag in the vault, most used first, and the fixed note types."""
    counts = note_writes.vault_tags(service)
    return TagList(
        types=list(tag_rules.TYPE_TAGS),
        tags=[TagCount(tag=t, notes=n) for t, n in counts.most_common()],
    )


@tags_router.post("/suggest", response_model=TagSuggestions)
def suggest_tags(payload: TagSuggestRequest, service: ServiceDep) -> TagSuggestions:
    """Existing tags that fit a note by meaning. Ask before creating the note."""
    found, checked = tag_rules.suggest_for_note(
        payload.title, payload.body, list(note_writes.vault_tags(service))
    )
    return TagSuggestions(
        suggestions=[TagSuggestion(tag=s.tag, score=s.score, by=s.by) for s in found],
        checked_meaning=checked,
    )
