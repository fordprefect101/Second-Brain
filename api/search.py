"""Unified search across every source.

Two pieces:

  index_all()  — read the sources, write rows into search_index
  search()     — query that index

The index is pure cache. Dropping search_index costs one reindex and nothing else
(ADR-002's drop test). It stores titles, tags, and a short excerpt — never note
bodies, because a cached body goes stale the moment the file is edited and a second
copy that can diverge makes this a second source of truth (Plan.md §2). The excerpt
length CHECK in schema.sql enforces that.

Keyword search only, deliberately. Semantic search arrives in the AI layer, once
there are real failed searches to justify it (ADR-007).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from api.entities import resolve_ids
from api.services import NoteService

EXCERPT_LIMIT = 500  # matches the CHECK constraint in schema.sql


@dataclass
class SearchHit:
    id: UUID
    title: str
    excerpt: str
    source: str
    rank: float


def _hash(text: str) -> str:
    """Content fingerprint, for detecting real changes.

    mtime is the cheap check and it lies: two edits in the same second look
    identical, and copy/sync tools rewrite timestamps unpredictably. The hash is
    the honest answer. Using both is why search_index has both columns.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def index_notes(conn: psycopg.Connection, service: NoteService) -> dict[str, int]:
    """Index every note from a provider. Returns counts of what changed.

    Incremental: a note whose content hash is unchanged is skipped rather than
    re-indexed. At nine notes that saves nothing; the point is that the logic exists
    before the vault is large enough for a full reindex to hurt.
    """
    notes = service.list_notes(limit=5000)
    stats = {"seen": len(notes), "indexed": 0, "skipped": 0, "removed": 0}

    if not notes:
        return stats

    ids = resolve_ids(
        conn,
        provider=service.source_id,
        entity_type="note",
        provider_ids=[n.provider_id for n in notes],
    )

    with conn.cursor() as cur:
        cur.execute(
            "select entity_id, content_hash from search_index where provider = %s",
            (service.source_id,),
        )
        known = {row["entity_id"]: row["content_hash"] for row in cur.fetchall()}

    for note in notes:
        entity_id = ids[note.provider_id]
        # Title and tags are part of the fingerprint: renaming or retagging a note
        # changes what should be searchable even when the body is untouched.
        fingerprint = _hash(f"{note.title}\n{note.excerpt}\n{','.join(note.tags)}")

        if known.get(entity_id) == fingerprint:
            stats["skipped"] += 1
            continue

        _upsert(
            conn,
            entity_id=entity_id,
            provider=service.source_id,
            title=note.title,
            excerpt=note.excerpt,
            tags=note.tags,
            modified_at=note.modified_at,
            content_hash=fingerprint,
        )
        stats["indexed"] += 1

    # Notes deleted from the vault leave rows behind. Nothing reports a deletion, so
    # it is inferred: anything indexed for this provider that the listing did not
    # return no longer exists.
    live_ids = set(ids.values())
    with conn.cursor() as cur:
        cur.execute(
            "delete from search_index where provider = %s and not (entity_id = any(%s))",
            (service.source_id, list(live_ids)),
        )
        stats["removed"] = cur.rowcount
    conn.commit()

    return stats


def index_captures(conn: psycopg.Connection) -> dict[str, int]:
    """Index captures so search covers them alongside notes.

    Captures live in Postgres already, so this is a table-to-table copy rather than
    a provider read. They still go through entity_map — search results must be able
    to name their source (Plan.md §12), and that needs uniform identity.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select id, body, kind, created_at from capture_items "
            "where status <> 'archived'"
        )
        rows = cur.fetchall()

    stats = {"seen": len(rows), "indexed": 0, "skipped": 0, "removed": 0}
    if not rows:
        return stats

    ids = resolve_ids(
        conn,
        provider="personal_os",
        entity_type="capture",
        provider_ids=[str(r["id"]) for r in rows],
    )

    with conn.cursor() as cur:
        cur.execute(
            "select entity_id, content_hash from search_index where provider = 'personal_os'"
        )
        known = {row["entity_id"]: row["content_hash"] for row in cur.fetchall()}

    for row in rows:
        entity_id = ids[str(row["id"])]
        body = row["body"]
        fingerprint = _hash(f"{body}\n{row['kind']}")

        if known.get(entity_id) == fingerprint:
            stats["skipped"] += 1
            continue

        _upsert(
            conn,
            entity_id=entity_id,
            provider="personal_os",
            # Captures have no title. The first line is the closest honest thing.
            title=body.splitlines()[0][:80],
            excerpt=body[:EXCERPT_LIMIT],
            tags=[row["kind"]],
            modified_at=row["created_at"],
            content_hash=fingerprint,
        )
        stats["indexed"] += 1

    live_ids = set(ids.values())
    with conn.cursor() as cur:
        cur.execute(
            "delete from search_index where provider = 'personal_os' "
            "and not (entity_id = any(%s))",
            (list(live_ids),),
        )
        stats["removed"] = cur.rowcount
    conn.commit()

    return stats


def _upsert(
    conn: psycopg.Connection,
    *,
    entity_id: UUID,
    provider: str,
    title: str,
    excerpt: str,
    tags: list[str],
    modified_at: datetime,
    content_hash: str,
) -> None:
    """Write one row, building the tsvector in SQL.

    Weighting matters more than it looks. setweight marks title terms 'A' and body
    terms 'B', and ts_rank scores A higher — so a note *called* "Audiotour" beats one
    that merely mentions it. Without this, title matches and passing mentions rank
    identically, which is almost never what you want.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into search_index
                (entity_id, provider, title, excerpt, tags,
                 source_modified_at, content_hash, indexed_at, document)
            values (%(id)s, %(provider)s, %(title)s, %(excerpt)s, %(tags)s,
                    %(modified)s, %(hash)s, now(),
                    setweight(to_tsvector('english', %(title)s), 'A') ||
                    setweight(to_tsvector('english', %(tagtext)s), 'A') ||
                    setweight(to_tsvector('english', %(excerpt)s), 'B'))
            on conflict (entity_id) do update set
                provider           = excluded.provider,
                title              = excluded.title,
                excerpt            = excluded.excerpt,
                tags               = excluded.tags,
                source_modified_at = excluded.source_modified_at,
                content_hash       = excluded.content_hash,
                indexed_at         = excluded.indexed_at,
                document           = excluded.document
            """,
            {
                "id": entity_id,
                "provider": provider,
                "title": title,
                "excerpt": excerpt,
                "tags": tags,
                "tagtext": " ".join(tags),
                "modified": modified_at,
                "hash": content_hash,
            },
        )


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    limit: int = 20,
    source: str | None = None,
) -> list[SearchHit]:
    """Keyword search with prefix matching.

    websearch_to_tsquery parses what people actually type — quoted phrases, OR, and
    -exclusions — instead of demanding tsquery's & and | syntax. It does not do
    prefix matching, so the last word is additionally matched as a prefix: typing
    "transcrip" finds "transcription" while you are still typing.

    The two queries are OR'd, so an exact match still wins on rank.
    """
    text = query.strip()
    if not text:
        return []

    last_word = text.split()[-1]
    # Strip characters that would be interpreted as tsquery operators.
    prefix = "".join(c for c in last_word if c.isalnum() or c == "_")

    with conn.cursor() as cur:
        cur.execute(
            """
            with q as (
                select websearch_to_tsquery('english', %(text)s) as exact,
                       case when %(prefix)s = '' then null
                            else to_tsquery('english', %(prefix)s || ':*')
                       end as prefix
            )
            select s.entity_id, s.title, s.excerpt, s.provider,
                   -- Score against whichever query actually matched, not just the
                   -- exact one: a prefix-only hit would otherwise rank 0.0 and sort
                   -- below everything. greatest() also means an exact match still
                   -- outranks a prefix match on the same row.
                   greatest(
                     case when q.exact  is null then 0
                          else ts_rank(s.document, q.exact) end,
                     case when q.prefix is null then 0
                          else ts_rank(s.document, q.prefix) end
                   ) as rank
              from search_index s, q
             -- The OR group MUST be parenthesised. AND binds tighter than OR, so
             -- without these brackets the source filter would apply only to the
             -- prefix branch and exact matches would ignore it entirely.
             where (
                     (q.exact is not null and s.document @@ q.exact)
                  or (q.prefix is not null and s.document @@ q.prefix)
                   )
               -- ::text because Postgres cannot infer a NULL parameter's type.
               and (%(source)s::text is null or s.provider = %(source)s::text)
             order by rank desc, s.source_modified_at desc nulls last
             limit %(limit)s
            """,
            {"text": text, "prefix": prefix, "source": source, "limit": limit},
        )
        rows = cur.fetchall()

    return [
        SearchHit(
            id=row["entity_id"],
            title=row["title"],
            excerpt=row["excerpt"] or "",
            source=row["provider"],
            rank=float(row["rank"]),
        )
        for row in rows
    ]
