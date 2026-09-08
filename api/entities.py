"""Internal identity for things owned by other services.

This is where entity_map finally does the job it was created for in step 2
(ADR-004). A provider hands back its own identifiers — a vault-relative path, a
Google task id — and this translates them into stable internal uuids.

Why bother, with one provider whose paths are already unique?

Because identity is the hardest thing to retrofit. Once URLs, search results, and
capture references all point at raw file paths, adding an indirection layer means
rewriting every one of them. And the internal id is what will eventually let a
GitHub repo, an Obsidian note, and a calendar event be recognised as the same
project — which no provider-native identifier can express.
"""

from __future__ import annotations

from uuid import UUID

import psycopg


def resolve_ids(
    conn: psycopg.Connection,
    provider: str,
    entity_type: str,
    provider_ids: list[str],
) -> dict[str, UUID]:
    """Map provider identifiers to internal ids, creating rows for unseen ones.

    One round trip for the whole batch. `last_seen_at` updates on every call, which
    is what will later distinguish "this note still exists" from "this note was
    deleted from the vault and its row should be cleaned up".

    Note the ON CONFLICT: the unique (provider, provider_id) constraint from
    schema.sql makes this idempotent, so re-listing a vault does not create
    duplicate identities.
    """
    if not provider_ids:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into entity_map (provider, provider_id, entity_type)
            select %s, pid, %s from unnest(%s::text[]) as pid
            on conflict (provider, provider_id)
                do update set last_seen_at = now()
            returning id, provider_id
            """,
            (provider, entity_type, provider_ids),
        )
        rows = cur.fetchall()
    conn.commit()

    return {row["provider_id"]: row["id"] for row in rows}


def lookup_provider_id(
    conn: psycopg.Connection, entity_id: UUID
) -> tuple[str, str] | None:
    """Internal id -> (provider, provider_id). The reverse direction.

    Used when a route receives an internal id and has to ask a provider for the
    underlying thing. Returns None if the id is unknown — which happens legitimately
    if the database was rebuilt (ADR-002), since ids are assigned, not computed.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select provider, provider_id from entity_map where id = %s",
            (entity_id,),
        )
        row = cur.fetchone()

    return (row["provider"], row["provider_id"]) if row else None
