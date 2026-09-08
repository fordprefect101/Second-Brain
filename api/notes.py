"""Note routes.

The layering from docs/architecture/phase-0.md, made concrete:

    route  ->  NoteService (Protocol)  ->  ObsidianVaultProvider  ->  filesystem

This module imports the Protocol, not the provider. The single line that picks
which provider to use lives in get_note_service() — that is the whole seam. Adding
Notion in Phase 4 means writing a provider and changing that function.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from pydantic import BaseModel

from api.captures import ConnDep
from api.config import config
from api.entities import lookup_provider_id, resolve_ids
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
