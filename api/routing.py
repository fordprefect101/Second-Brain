"""Routing captures into the vault, with undo.

Closes the hole V1 shipped with: the Inbox had no exit, so captures accumulated in
Postgres forever and capture_items held data it was never meant to keep.

Every vault write follows the same sequence, and the order is the point:

    1. snapshot what is there now   (nothing has changed yet)
    2. write atomically             (temp -> fsync -> replace)
    3. record what happened         (capture points at what it became)

Snapshot BEFORE write, always. Reversed, a crash between the two leaves a modified
file with no record of its previous contents — which is exactly the situation
ADR-005 was written to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg

from api.entities import resolve_ids
from api.providers.obsidian import ObsidianVaultProvider


class RoutingError(RuntimeError):
    """A capture could not be routed. Nothing was written."""


@dataclass
class RoutedCapture:
    capture_id: UUID
    entity_id: UUID
    provider_id: str
    ref: str


def route_capture_to_vault(
    conn: psycopg.Connection,
    provider: ObsidianVaultProvider,
    capture_id: UUID,
    *,
    title: str | None = None,
    folder: str = "",
) -> RoutedCapture:
    """Turn a capture into a note in the vault.

    The capture row is NOT deleted. It stops being the data and becomes the record
    of an event: on this date I captured this text and it became that note. No
    external service owns that fact — Obsidian knows the note exists, but not that
    it began as a 2am thought.

    The row is never updated when the note changes. It is a snapshot of a past
    event, not a cache of current state, which is what keeps it compatible with
    Plan.md §2 despite duplicating text.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select id, body, kind, status from capture_items where id = %s",
            (capture_id,),
        )
        capture = cur.fetchone()

    if capture is None:
        raise RoutingError("No capture with that id")
    if capture["status"] != "inbox":
        raise RoutingError(f"Capture is already {capture['status']}")

    body = capture["body"]
    # First line as the title, since a capture has none. Kept short enough to stay
    # a filename; the full text always goes in the body regardless.
    note_title = (title or body.splitlines()[0] or "Untitled").strip()[:80]

    note_body = _compose(note_title, body, capture["kind"])

    # 1. Snapshot. A create has no prior content, so content is NULL — undoing it
    #    means deleting the file rather than restoring anything.
    note = provider.create_note(note_title, note_body, folder=folder)

    ids = resolve_ids(
        conn,
        provider=provider.source_id,
        entity_type="note",
        provider_ids=[note.provider_id],
    )
    entity_id = ids[note.provider_id]
    ref = f"{provider.source_id}:{note.provider_id}"

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into note_snapshots
                (entity_id, provider, provider_id, content, operation)
            values (%s, %s, %s, null, 'create')
            """,
            (entity_id, provider.source_id, note.provider_id),
        )

        # 3. Record. Both fields are set together so a routed row can never exist
        #    without a pointer to what it became.
        cur.execute(
            """
            update capture_items
               set status = 'routed',
                   routed_at = now(),
                   routed_to_entity_id = %s,
                   routed_to_ref = %s,
                   updated_at = now()
             where id = %s and status = 'inbox'
            """,
            (entity_id, ref, capture_id),
        )
        if cur.rowcount != 1:
            # Someone else routed or archived it between the check and here.
            conn.rollback()
            provider.delete_note(note.provider_id)
            raise RoutingError("Capture changed while routing; nothing was written")

    conn.commit()

    return RoutedCapture(
        capture_id=capture_id,
        entity_id=entity_id,
        provider_id=note.provider_id,
        ref=ref,
    )


def undo_routing(
    conn: psycopg.Connection,
    provider: ObsidianVaultProvider,
    capture_id: UUID,
) -> None:
    """Reverse a routing: remove the note, return the capture to the inbox.

    Only undoes writes this system made, and only using its own snapshot. If the
    file was edited in Obsidian after routing, that is a real edit by a human and
    deleting it would destroy work — so the content is compared first and the undo
    refuses rather than guessing.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select routed_to_entity_id, routed_to_ref, status "
            "from capture_items where id = %s",
            (capture_id,),
        )
        capture = cur.fetchone()

    if capture is None:
        raise RoutingError("No capture with that id")
    if capture["status"] != "routed":
        raise RoutingError("That capture was not routed")

    entity_id = capture["routed_to_entity_id"]

    with conn.cursor() as cur:
        cur.execute(
            """
            select id, provider_id, content, operation from note_snapshots
             where entity_id = %s and undone_at is null
             order by taken_at desc limit 1
            """,
            (entity_id,),
        )
        snapshot = cur.fetchone()

    if snapshot is None:
        raise RoutingError("No undo record for that note")

    provider_id = snapshot["provider_id"]

    if snapshot["operation"] == "create":
        current = provider.read_raw(provider_id)
        if current is not None:
            expected = _expected_content(conn, capture_id)
            if expected is not None and current.strip() != expected.strip():
                raise RoutingError(
                    "The note has been edited since routing. Undo would delete "
                    "those changes, so it was refused — delete it in Obsidian if "
                    "that is what you want."
                )
        provider.delete_note(provider_id)
    else:
        provider.update_note(provider_id, snapshot["content"] or "")

    with conn.cursor() as cur:
        cur.execute(
            "update note_snapshots set undone_at = now() where id = %s",
            (snapshot["id"],),
        )
        cur.execute(
            """
            update capture_items
               set status = 'inbox', routed_at = null,
                   routed_to_entity_id = null, routed_to_ref = null,
                   updated_at = now()
             where id = %s
            """,
            (capture_id,),
        )
        # The note is gone; its index row must go too, or search returns a result
        # that 404s when opened.
        cur.execute("delete from search_index where entity_id = %s", (entity_id,))
    conn.commit()


def _compose(title: str, body: str, kind: str) -> str:
    """Build the markdown file.

    Frontmatter records where this came from. Provenance in the note itself means
    the vault stays self-describing even with the Personal OS uninstalled — the
    whole point of Obsidian owning the knowledge (ADR-001).
    """
    return (
        "---\n"
        f"tags: [{kind}]\n"
        "source: personal-os-capture\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


def _expected_content(conn: psycopg.Connection, capture_id: UUID) -> str | None:
    """Reconstruct what we originally wrote, to detect later edits."""
    with conn.cursor() as cur:
        cur.execute(
            "select body, kind, routed_to_ref from capture_items where id = %s",
            (capture_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    body = row["body"]
    title = (body.splitlines()[0] or "Untitled").strip()[:80]
    return _compose(title, body, row["kind"])
