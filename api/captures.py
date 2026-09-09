"""Captures — the one domain the Personal OS owns.

Note the missing layer. Every other domain will go

    route -> service interface -> provider -> external API

because an external service owns the data. Captures have no provider: a raw,
unclassified thought has no home yet, so there is nothing to abstract over. Adding
a `CaptureService` Protocol here would be indirection with exactly one possible
implementation, forever.

That asymmetry is deliberate. Abstractions earn their place by having more than one
thing behind them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from api.database import connect

router = APIRouter(prefix="/captures", tags=["captures"])

CaptureKind = Literal["note", "idea", "task", "resource", "reminder"]
CaptureStatus = Literal["inbox", "routed", "archived"]

# The columns every response needs. Defined once so the SELECT and the RETURNING
# clauses cannot drift apart.
COLUMNS = "id, body, kind, status, created_at, routed_to_ref"


def get_conn():
    """One connection per request.

    Deliberately not pooled. A single-user local app makes a handful of requests a
    minute, and psycopg's connect() to a container on localhost costs single-digit
    milliseconds. Pooling would be complexity without a problem (Plan.md §4).

    Revisit when: the API serves concurrent requests, or connection setup shows up
    in a latency measurement. Both are measurable, so this is a decision that can be
    reopened with evidence rather than by taste.
    """
    with connect() as conn:
        conn.row_factory = dict_row
        yield conn


ConnDep = Annotated[psycopg.Connection, Depends(get_conn)]


class CamelModel(BaseModel):
    """Serialises to camelCase for the browser, stays snake_case in Python.

    The alternative is camelCase field names in Python or snake_case keys in
    TypeScript — either way one language reads badly. This keeps both idiomatic and
    puts the translation in exactly one place.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CaptureCreate(CamelModel):
    # min_length=1 mirrors the CHECK constraint in schema.sql. Validated in both
    # places on purpose: pydantic gives a 422 with a useful message, the constraint
    # guarantees the rule even if something writes to the table directly.
    body: str = Field(min_length=1, max_length=10_000)
    kind: CaptureKind


class Capture(CamelModel):
    id: UUID
    body: str
    kind: CaptureKind
    status: CaptureStatus
    created_at: datetime
    routed_to_ref: str | None = None


@router.get("", response_model=list[Capture])
def list_captures(
    conn: ConnDep,
    status: CaptureStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[dict]:
    """Newest first. Optionally filtered by status."""
    with conn.cursor() as cur:
        if status is None:
            cur.execute(
                f"select {COLUMNS} from capture_items "
                "order by created_at desc limit %s",
                (limit,),
            )
        else:
            cur.execute(
                f"select {COLUMNS} from capture_items where status = %s "
                "order by created_at desc limit %s",
                (status, limit),
            )
        return cur.fetchall()


@router.post("", response_model=Capture, status_code=201)
def create_capture(payload: CaptureCreate, conn: ConnDep) -> dict:
    """Store a capture. Classification is manual — no AI (Plan.md §11)."""
    with conn.cursor() as cur:
        cur.execute(
            f"insert into capture_items (body, kind) values (%s, %s) returning {COLUMNS}",
            (payload.body.strip(), payload.kind),
        )
        row = cur.fetchone()
    conn.commit()
    return row


class RouteRequest(CamelModel):
    """Optional overrides. Both default to something sensible."""

    title: str | None = None
    folder: str = ""


class RouteResult(CamelModel):
    capture_id: UUID
    entity_id: UUID
    ref: str


@router.post("/{capture_id}/route", response_model=RouteResult)
def route_capture(capture_id: UUID, payload: RouteRequest, conn: ConnDep) -> RouteResult:
    """Turn a capture into a note in the vault.

    The first operation in this system that writes to the user's own files, so it
    goes through the audited path in api/routing.py: snapshot, atomic write, record.
    Reversible via /undo.
    """
    # Imported here rather than at module scope to keep captures.py independent of
    # the vault — captures work perfectly well with no Obsidian configured.
    from api.notes import get_note_service
    from api.routing import RoutingError, route_capture_to_vault

    service = get_note_service()  # raises 503 if no vault is configured

    try:
        routed = route_capture_to_vault(
            conn, service, capture_id, title=payload.title, folder=payload.folder
        )
    except RoutingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not write to the vault: {exc}"
        ) from exc

    return RouteResult(
        capture_id=routed.capture_id, entity_id=routed.entity_id, ref=routed.ref
    )


@router.post("/{capture_id}/undo-route", response_model=Capture)
def undo_route(capture_id: UUID, conn: ConnDep) -> dict:
    """Undo a routing: delete the note, return the capture to the inbox.

    Refuses if the note was edited in Obsidian after routing — those are real edits
    by a person, and deleting them would destroy work.
    """
    from api.notes import get_note_service
    from api.routing import RoutingError, undo_routing

    service = get_note_service()

    try:
        undo_routing(conn, service, capture_id)
    except RoutingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    with conn.cursor() as cur:
        cur.execute(f"select {COLUMNS} from capture_items where id = %s", (capture_id,))
        return cur.fetchone()


@router.post("/{capture_id}/archive", response_model=Capture)
def archive_capture(capture_id: UUID, conn: ConnDep) -> dict:
    """Dismiss without routing anywhere.

    Routing to Obsidian is Phase 2b, gated on an undo mechanism (ADR-005). Archive
    is the triage action available until then — it is reversible in the data even
    though the UI does not expose an un-archive yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"update capture_items set status = 'archived', updated_at = now() "
            f"where id = %s and status = 'inbox' returning {COLUMNS}",
            (capture_id,),
        )
        row = cur.fetchone()

    if row is None:
        # Ambiguous by design: either it does not exist or it was not in the inbox.
        # Distinguishing them would need a second query to say something the caller
        # cannot act on differently.
        raise HTTPException(status_code=404, detail="No inbox capture with that id")

    conn.commit()
    return row
